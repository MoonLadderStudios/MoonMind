# Live Logs and Session-Aware Run Observability in MoonMind

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-04-08

**Replaces or substantially rewrites:**
- `docs/Temporal/LiveTaskManagement.md`
- `specs/084-live-log-tailing/spec.md`

**Related:**
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ArtifactPresentationContract.md`](../Temporal/ArtifactPresentationContract.md)
- [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md)

## 1. Summary

MoonMind should treat **Live Logs** as a **session-aware observability timeline**, not as an embedded terminal session and not as a thin tail of `stdout` and `stderr` alone.

For managed agent runs, live visibility should be based on:

- durable `stdout` and `stderr` artifact capture
- structured diagnostics
- durable session continuity and control-boundary artifacts when the runtime exposes a managed session plane
- bounded workflow metadata
- optional MoonMind-owned live streaming to Mission Control
- explicit intervention controls that remain separate from logging

With this design:
- **OAuth** continues to use `xterm.js` plus a MoonMind PTY/WebSocket bridge because OAuth is an interactive terminal flow
- **managed run logs** do **not** use `xterm.js` and do **not** depend on terminal embedding
- **Codex managed sessions** surface session continuity events such as epoch boundaries, thread changes, turn lifecycle, approvals, and reset boundaries inside the same operator-visible observability timeline
- Mission Control shows logs through a MoonMind-native React viewer backed by MoonMind APIs and artifacts, implemented with `react-virtuoso` for virtualized rendering, `anser` for ANSI parsing, TanStack Query for retrieval state, and SSE via `EventSource` for live follow mode

This keeps observability deterministic, persistent, auditable, and independent from interactive terminal transport while still preserving continuity visibility for the Codex managed session plane.

### 1.1 Implementation tracking

This document is the canonical desired-state architecture for managed-run observability.

Rollout sequencing, implementation status, and remaining migration work belong in [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md).

---

## 2. Why the previous live-logs shape no longer fits

The older tmate-oriented design no longer fits because it assumed that observability could be treated as a terminal-viewing problem.

The first Live Logs redesign correctly replaced that with an artifact-first model, but its center of gravity was still mostly **run-centric** and **line-centric**:
- a run emits `stdout` and `stderr`
- MoonMind adds a few `system` annotations
- Mission Control shows a merged tail and optionally follows live SSE

That is no longer sufficient for the Codex managed session plane.

The Codex session-plane contract introduces a richer continuity model:
- one task-scoped managed session container per task
- one active Codex thread per session epoch
- bounded session identity made of `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id`
- explicit `clear_session` semantics that write artifacts, increment the epoch, create a new thread, and require UI-visible boundary presentation
- a durable-state rule that says operator presentation must come from artifacts and bounded workflow metadata rather than container-local state

That means operators need to observe more than raw process bytes.

They need a timeline that can also answer:
- Which session epoch am I looking at?
- Did the session get cleared?
- Which thread is active now?
- Which turn is currently running?
- Was a control action or approval involved?
- Which continuity artifacts were published?

The right model is therefore:

> **Live Logs is a projection over run observability events.**
>
> It is not the source of truth.

The source of truth remains:
- durable artifacts
- bounded workflow metadata
- MoonMind-owned structured observability records

---

## 3. Core decision

MoonMind should adopt these rules:

1. **Managed run observability is artifact-first, MoonMind-owned, and session-aware.**
   Every managed run should produce durable stdout/stderr artifacts, a diagnostics artifact, continuity and control-boundary artifacts when a managed session plane is active, structured run metadata, and optional live streaming through MoonMind APIs.
2. **Live Logs is a projection over a normalized run observability model.**
3. **Logging is not intervention.** The Live Logs panel is passive observation only.
4. **`xterm.js` is reserved for OAuth and other interactive terminal flows, not for managed run logs.**
5. **Codex session continuity is observable inside Live Logs, but control remains separate.**
6. **Container state is a continuity and performance cache, not durable truth.**

---

## 4. Goals

### 4.1 Primary goals

The observability system should:
- work even when no interactive terminal session exists
- preserve logs and diagnostics after the run ends
- preserve session continuity facts after the run ends
- support live observation for active runs without making live delivery authoritative
- show session identity and reset boundaries in a way operators can understand quickly
- merge `stdout`, `stderr`, `system`, and session-plane events into one coherent timeline
- survive reconnects, refreshes, and temporary disconnects
- keep the task detail page simple and operator-friendly
- avoid dependence on terminal relays, browser attachment, or container-local scrollback
- preserve backward-compatible live-log transport and UI lifecycle where that still makes sense

### 4.2 Non-goals

This system does not aim to:
- provide shell access for ordinary task runs
- mirror the raw Codex App Server protocol 1:1 into the browser
- make logs depend on container-local databases or runtime home directories as durable truth
- replace provider-native intervention channels
- implement distributed full-text log indexing in the first version
- implement a terminal-grade durable replay buffer for every transient event
- introduce cross-task session reuse or a generic runtime marketplace

---

## 5. User experience

## 5.1 Task detail page

The task detail page should include an **Observability** section with these panels:
- **Live Logs**
- **Stdout**
- **Stderr**
- **Diagnostics**
- **Artifacts**

A separate continuity drill-down surface may also exist, but Live Logs must still inline the most important session-plane milestones so operators do not need to jump between unrelated observability views.

The default operator workflow should be:
1. open task
2. inspect the relevant expanded step row in the Steps section
3. open Live Logs for that step's managed run when `taskRunId` is present
4. inspect the current session snapshot shown at the top of the panel
5. read the merged session-aware timeline
6. inspect Stdout, Stderr, Diagnostics, or continuity artifacts as needed
7. use separate Intervention controls if action is required

Integration rules:
- Logs and Diagnostics should open inside the expanded step when a step row has `taskRunId`
- the default fetch sequence remains `observability-summary` → structured or merged initial tail → optional SSE live follow
- task-level observability panels remain valid for top-level managed runs, but step-contextual consumption is still the primary operator flow for plan-driven work

## 5.2 Live Logs panel

The Live Logs panel should be a MoonMind-native log viewer, not an embedded terminal.

Behavior:
- initially loads the most recent visible content from MoonMind APIs; **initial visible content must not depend on SSE success**
- prefers a structured observability timeline when available
- may fall back to the existing artifact-backed merged tail for historical or partially migrated runs
- upgrades to a live stream only when the run is active and live streaming is supported
- shows provenance per row or chunk, including `stdout`, `stderr`, `system`, and session-plane events
- shows the current session snapshot in the panel header: `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id` when available
- renders epoch boundaries and reset boundaries as explicit timeline banners rather than burying them as plain text
- can reconnect from the last known `sequence`; resume is best-effort and artifacts remain the durable fallback
- falls back to artifact-backed mode if live streaming is unavailable or reconnect fails
- stops streaming when collapsed or when the tab is backgrounded
- **ended runs never attempt live-stream connection**; the final artifact-backed timeline remains available

## 5.3 Timeline row types

The merged Live Logs timeline should support at least these row types:
- output rows for `stdout` and `stderr`
- `system` annotations from MoonMind supervision
- session lifecycle rows such as `session_started`, `session_resumed`, `session_cleared`, and `session_terminated`
- turn lifecycle rows such as `turn_started`, `turn_completed`, and `turn_interrupted`
- approval rows such as `approval_requested` and `approval_resolved`
- continuity publication rows such as `summary_published`, `checkpoint_published`, and `reset_boundary_published`

The viewer may visually group or badge these rows differently, but they remain part of one chronological timeline ordered by the shared run-global `sequence`.

## 5.4 Stdout and Stderr panels

These remain artifact-backed viewers for the individual streams.

They should support:
- tail view
- full download
- simple copy
- line wrapping toggle
- light filtering or search later

## 5.5 Diagnostics panel

Diagnostics should show structured metadata such as:
- run status
- exit code
- duration
- failure class
- timestamps
- parsed warnings or errors
- rate-limit detection
- artifact refs
- latest session snapshot
- latest continuity artifact refs
- summary

## 5.6 Continuity drill-down

MoonMind may expose a separate continuity drill-down surface for richer session artifacts such as:
- latest session summary
- latest checkpoint
- latest control event
- latest reset boundary

That drill-down is useful, but it is not a replacement for inline Live Logs observability.

---

## 6. Architectural model

## 6.1 Observation plane

The observation plane owns passive visibility.

Components:
- launcher
- supervisor
- Codex managed session adapter
- log and session-event capture pipeline
- diagnostics builder
- continuity artifact publisher
- structured observability event writer
- live log stream publisher
- Mission Control observability APIs
- artifact-backed log retrieval

## 6.2 Control plane

The control plane owns intervention.

Components:
- Temporal signals and updates
- provider-native messaging and control adapters
- approvals
- audit trail for intervention actions

## 6.3 OAuth plane

OAuth uses a different transport because it is interactive.

Components:
- `xterm.js`
- OAuth PTY/WebSocket bridge
- short-lived auth container

That plane remains explicitly separate from the managed-run log viewer.

## 6.4 Cross-process transport boundary

**Critical constraint:** the live stream publisher must be cross-process or otherwise shared across the producer and API/UI boundary.

An API-process-local in-memory publisher is **not** sufficient as the canonical architecture boundary.

Rules:
- the live stream transport must not assume the log producer runs in the same API process
- a process-local replay buffer may exist as an optimization, but it is not the architecture boundary
- live publication must target a shared MoonMind observability transport such as Redis pub/sub, a shared append-only spool, or DB-backed tailing
- the current implementation choice of a shared append-only spool file under the run workspace remains valid

## 6.5 Projection model

MoonMind should standardize on one normalized event model for run observability.

Different operator surfaces are projections over that model:
- **Live Logs** → merged timeline projection
- **Stdout** → filtered raw stream projection
- **Stderr** → filtered raw stream projection
- **Diagnostics** → structured summary projection
- **Continuity drill-down** → session artifact projection

This avoids inventing separate ad hoc observability contracts for each panel.

---

## 7. Canonical observability contract

Every managed run must produce durable observability outputs.

## 7.1 Durable truth

Required durable outputs:
- `stdout_artifact_ref`
- `stderr_artifact_ref`
- `diagnostics_ref`
- optional `merged_log_artifact_ref`
- durable session continuity artifacts such as:
  - `session.summary`
  - `session.checkpoint`
  - `session.control_event`
  - `session.reset_boundary`
- structured run summary metadata
- bounded session snapshot metadata

Artifacts and bounded metadata are authoritative.

They must support recovery, audit, and operator presentation even when:
- the browser never connected
- the live stream failed
- the container was restarted
- the container-local state was lost

## 7.2 Optional live observability outputs

Optional convenience metadata:
- `live_stream_id`
- `live_stream_status`
- `last_log_offset`
- `last_log_at`
- `supports_live_streaming`

These fields are non-authoritative convenience metadata.

Artifacts and bounded workflow metadata remain authoritative.

## 7.3 Session snapshot metadata

The observability summary for a managed run should expose the latest known session snapshot when available:
- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`
- `latest_session_summary_ref`
- `latest_session_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `pending_approval_count` or equivalent bounded approval summary

This snapshot is for operator orientation and UI rendering.

It must not imply that container-local state is authoritative.

## 7.4 Canonical event model

The normalized observability payload should be a MoonMind-owned event record.

Suggested shape:

```ts
type RunObservabilityEvent = {
  runId: string
  sequence: number
  timestamp: string
  stream: "stdout" | "stderr" | "system" | "session"
  kind:
    | "stdout_chunk"
    | "stderr_chunk"
    | "system_annotation"
    | "session_started"
    | "session_resumed"
    | "session_cleared"
    | "session_terminated"
    | "turn_started"
    | "turn_completed"
    | "turn_interrupted"
    | "approval_requested"
    | "approval_resolved"
    | "summary_published"
    | "checkpoint_published"
    | "reset_boundary_published"
  text: string
  offset?: number | null
  sessionId?: string | null
  sessionEpoch?: number | null
  containerId?: string | null
  threadId?: string | null
  turnId?: string | null
  activeTurnId?: string | null
  metadata?: Record<string, unknown> | null
}
```

Rules:
- `sequence` is a run-global monotonically increasing namespace shared across all streams and event kinds
- `text` remains the raw display text used by the timeline
- `kind` carries semantic meaning for presentation and filtering
- session-plane fields are optional so older and historical runs can still render
- new fields must be additive so existing clients that only understand `sequence`, `stream`, `offset`, `timestamp`, and `text` can still degrade safely

## 7.5 Event classes

MoonMind should normalize at least these event classes:

### Output events
- `stdout_chunk`
- `stderr_chunk`

### MoonMind system events
- supervisor lifecycle annotations
- fallback and degradation annotations
- persistence and diagnostics annotations

### Session lifecycle events
- `session_started`
- `session_resumed`
- `session_cleared`
- `session_terminated`

### Turn lifecycle events
- `turn_started`
- `turn_completed`
- `turn_interrupted`

### Control-boundary and continuity events
- `summary_published`
- `checkpoint_published`
- `reset_boundary_published`

### Approval events
- `approval_requested`
- `approval_resolved`

## 7.6 Merged-tail contract

The merged-tail endpoint (`/logs/merged`) may return content computed in one of three ways:
- from a pre-built `merged_log_artifact_ref` if one exists
- synthesized on demand from structured observability events when they exist durably
- synthesized from `stdout_artifact_ref` + `stderr_artifact_ref` + spool metadata if no structured merged artifact exists

Rules for merged tail:
- ordering uses the shared run-global `sequence`
- `stdout`, `stderr`, `system`, and session events may all appear in merged output
- `merged_log_artifact_ref` is optional
- the endpoint must handle partial artifacts and still return whatever durably captured content is available
- when a historical run lacks structured event history and spool metadata, the endpoint may fall back to labeled `stdout`/`stderr` concatenation with an explicit warning that chronological session-aware ordering is unavailable

---

## 8. Codex managed session integration

## 8.1 Session-plane ownership

The Codex CLI managed session plane is responsible for adapting provider-native session activity into MoonMind-owned observability outputs.

It must publish:
- continuity artifacts
- bounded session snapshot updates
- normalized observability events

The browser must not depend on raw provider-native protocol payloads.

## 8.2 Stable MoonMind control vocabulary

The stable MoonMind-side control vocabulary remains:
- `start_session`
- `resume_session`
- `send_turn`
- `steer_turn`
- `interrupt_turn`
- `clear_session`
- `cancel_session`
- `terminate_session`

These actions belong to the control plane.

Their observable consequences should also appear in the observation plane as passive rows and artifacts.

## 8.3 Clear / reset semantics in observability

`clear_session` is not a terminal slash-command emulation.

The observability contract for `clear_session` is:
1. write a `session.control_event` artifact
2. write a `session.reset_boundary` artifact
3. increment `session_epoch`
4. start a new Codex thread inside the same container
5. clear `active_turn_id`
6. emit observable timeline rows that make the boundary obvious

Rules:
- clear/reset preserves `session_id`
- clear/reset preserves `container_id`
- clear/reset requires a new `thread_id`
- Live Logs and API consumers must present the new epoch boundary explicitly

## 8.4 Session event mapping guidance

The normalized event stream should represent at least these MoonMind-side semantics:
- `start_session` → `session_started`
- `resume_session` → `session_resumed`
- `send_turn` → `turn_started`
- `interrupt_turn` → `turn_interrupted`
- successful turn completion → `turn_completed`
- `clear_session` → `session_cleared` plus `reset_boundary_published`
- continuity artifact publication → `summary_published` or `checkpoint_published`
- approval-required workflow pauses → `approval_requested`
- approval resolution → `approval_resolved`

The adapter may use Codex App Server or another internal harness behind the boundary, but the MoonMind browser contract remains normalized and stable.

---

## 9. Runtime execution model

## 9.1 Launcher

The launcher must start managed agent runs using the managed runtime model for that provider.

For raw subprocess-based managed runs, this still means direct pipe capture with:
- `stdout=PIPE`
- `stderr=PIPE`

For Codex managed sessions, the launcher and session-plane runtime must still ensure that raw `stdout` and `stderr` from the managed environment are durably captured where applicable.

The managed path must not depend on tmate wrapping for observability.

## 9.2 Supervisor and session adapter

The supervisor/session adapter owns:
- concurrent `stdout` and `stderr` draining where those streams exist
- session lifecycle observation
- turn lifecycle observation
- heartbeats
- timeout handling
- exit classification
- diagnostics generation
- final state persistence
- emission of live observability records for active subscribers
- updating `last_log_at` and `last_log_offset`
- updating the bounded session snapshot
- generation of MoonMind-owned `system` annotations where useful
- handoff of live chunks and session events to the shared live-stream transport boundary

**Durability comes first. Live publication is secondary.**

Live publication failure must never break artifact persistence, continuity artifact publication, or run completion.

## 9.3 Capture pipeline

For each observed output chunk or lifecycle event:
1. append to durable storage, spool, or event journal
2. update artifact-building state
3. update bounded run/session summary metadata
4. optionally publish to live subscribers via the shared transport boundary
5. update stream metadata such as `last_log_at` and `last_log_offset`

Durability comes first. Live streaming is secondary.

## 9.4 MoonMind-owned system annotations

MoonMind-generated `system` annotations are useful when MoonMind can add operator value without paraphrasing raw process output.

They are explicitly separate from raw `stdout` and `stderr` artifacts.

Good use cases include:
- run lifecycle transitions
- workspace preparation outcomes
- first-output visibility
- inactivity and fallback behavior
- degradation notices
- diagnostics persistence
- session boundary explanation
- approval wait explanation

Do not use `system` annotations to:
- simulate model reasoning
- paraphrase normal `stdout` or `stderr`
- spam heartbeats with no operator value
- replace explicit session-plane event kinds where those exist

---

## 10. Selected implementation baseline

The phrase "MoonMind-native log viewer" is intentionally **not** a terminal emulator and **not** a third-party hosted logging product.

It means a MoonMind-owned UI and API surface built on a small number of explicit libraries.

### 10.1 Backend event tools

MoonMind should standardize on:
- `structlog` for MoonMind-generated structured events
- direct subprocess pipe capture for managed-agent `stdout` and `stderr`
- structured continuity artifacts for session-plane milestones
- OpenTelemetry for trace and metric correlation
- FastAPI or Starlette SSE streaming for live log delivery

#### Rule: `stdout` and `stderr` are not `structlog`

Managed-agent output must remain direct pipe capture from the launched process.

MoonMind must not transform managed-agent `stdout` or `stderr` into framework-owned application logs before persistence, because doing so risks:
- losing byte-order fidelity
- losing raw stream separation
- introducing formatting noise
- making artifact replay differ from the original process output

`structlog` is reserved for MoonMind-owned events such as:
- supervisor lifecycle events
- reconnect notices
- truncation warnings
- timeout classification
- live-stream state changes
- system annotations
- session-boundary annotations

### 10.2 Frontend viewer foundation

The Mission Control Live Logs panel should be implemented as a native React component using:
- `react-virtuoso` for virtualized rendering of long and growing timelines
- `anser` for ANSI parsing into styled spans
- browser `EventSource` for SSE live follow mode
- TanStack Query for initial fetches, fallback retrieval, and cache invalidation

This combination is preferred over terminal or editor widgets because the Live Logs panel is a passive observability surface, not an interactive shell and not a code editor.

### 10.3 Rendering contract

The API remains authoritative for raw text and event identity. The UI is responsible for presentation.

Each live or fetched observability record should contain:
- `sequence`
- `stream`
- `kind`
- `offset`
- `timestamp`
- `text`
- optional session snapshot fields

The backend should not send pre-rendered HTML log fragments.

### 10.4 Viewer capability requirements

The selected frontend implementation must support:
- follow-tail mode
- reconnect from last sequence
- line wrapping toggle
- copy selected or visible lines
- per-row provenance
- artifact-backed initial load
- artifact fallback when live streaming is unavailable
- explicit epoch boundary rendering
- future inline annotations for approvals or other control-boundary events

---

## 11. Live streaming design

## 11.1 Transport

Use MoonMind-owned streaming.

Recommended first version:
- **Server-Sent Events** for simple operator consumption

Possible later version:
- WebSocket if richer interactive behaviors are needed

SSE is sufficient for one-way live observability.

## 11.2 Stream endpoint

Canonical endpoint:

`GET /api/task-runs/{id}/logs/stream`

Optional query params may include:
- `since=`
- `streams=stdout,stderr,system,session`
- `kinds=`
- `session_epoch=`

Example payload:

```json
{
  "runId": "run_123",
  "sequence": 42,
  "timestamp": "2026-04-08T20:14:03Z",
  "stream": "session",
  "kind": "reset_boundary_published",
  "text": "Session cleared. Starting epoch 3 on a new Codex thread.",
  "sessionId": "sess_abc",
  "sessionEpoch": 3,
  "containerId": "ctr_123",
  "threadId": "thread_789",
  "activeTurnId": null,
  "metadata": {
    "previousThreadId": "thread_456"
  }
}
```

Expected HTTP behavior:
- **active run, streaming available**: respond with `200 text/event-stream` and stream events
- **active run, streaming not supported**: respond with `200` and an appropriate status event or empty stream; caller falls back to artifact-backed retrieval
- **ended run**: respond with `200` and a single terminal event or empty stream indicating `ended`; caller must not reconnect
- **artifacts missing or partial**: the stream endpoint returns whatever is available; the summary indicates artifact status
- **stream unavailable**: respond with `503` or an error event; caller transitions to artifact-backed mode

## 11.3 Reconnect behavior

Mission Control should reconnect using the last known `sequence`.

For the live SSE path, the canonical resume cursor is the run-global `sequence` shared across all streams and event kinds.

**Resume is best-effort.**
- resume works while the live-stream backend still retains the replay window
- if the replay window has expired or the backend restarted, resume falls back to durable retrieval
- artifacts and structured observability history define what happened

If the live stream cannot resume:
- fetch the latest structured or merged artifact-backed tail
- render the latest visible content
- re-enter live mode only if the run is still active and streaming is available

## 11.4 Background-tab behavior

When the tab is backgrounded or the panel is collapsed:
- disconnect the live stream
- do not continue background streaming
- reconnect only if the panel is open and the run is still active

---

## 12. Artifact-backed observability APIs

MoonMind-owned backend APIs are required.

The old `web_ro`-style design is no longer the observability architecture.

## 12.1 Observability summary

`GET /api/task-runs/{id}/observability-summary`

Minimum required response fields:
- `run_id`
- `status`
- `stdout_artifact_ref`
- `stderr_artifact_ref`
- `diagnostics_ref`
- `merged_log_artifact_ref`
- `supports_live_streaming`
- `live_stream_id`
- `live_stream_status` (`available`, `ended`, `unavailable`)
- `last_log_at`
- `last_log_offset`
- `intervention_capabilities`
- `active_session_snapshot` or equivalent structured session fields:
  - `session_id`
  - `session_epoch`
  - `container_id`
  - `thread_id`
  - `active_turn_id`
- latest continuity refs or equivalent summary fields:
  - `latest_session_summary_ref`
  - `latest_session_checkpoint_ref`
  - `latest_control_event_ref`
  - `latest_reset_boundary_ref`

Behavior when the run is terminal:
- `live_stream_status` must be `ended` or `unavailable`
- `supports_live_streaming` must be `false` or the API must clearly signal that no stream connection is appropriate
- artifact refs remain populated and queryable indefinitely

Behavior when live streaming is unsupported:
- `supports_live_streaming: false`
- UI falls through to artifact-backed retrieval without attempting SSE

Behavior when artifacts are missing or partial:
- refs are `null`; the UI shows whatever is available
- partial artifacts should still be retrievable via tail endpoints

## 12.2 Structured observability events

`GET /api/task-runs/{id}/observability/events`

This endpoint is the preferred initial-load surface for a session-aware Live Logs timeline.

It should support at least:
- `since=`
- `limit=`
- `streams=`
- `kinds=`
- `session_epoch=`
- `thread_id=`

Rules:
- historical reads must come from durable storage or durable artifacts, not only from the transient live stream
- the endpoint may return a bounded tail window rather than the full history by default
- when a run predates structured event persistence, the system may fall back to the plain merged-tail path

## 12.3 Tail endpoints

These remain required compatibility and operator convenience endpoints:
- `GET /api/task-runs/{id}/logs/stdout`
- `GET /api/task-runs/{id}/logs/stderr`
- `GET /api/task-runs/{id}/logs/merged`

Each endpoint must handle:
- ended runs: return final artifact tail, no stream connection
- missing artifacts: return empty body or appropriate empty response
- partial artifacts: return whatever has been durably captured so far

## 12.4 Full retrieval and download

Required download surfaces:
- `GET /api/task-runs/{id}/logs/stdout/download`
- `GET /api/task-runs/{id}/logs/stderr/download`
- `GET /api/task-runs/{id}/diagnostics`

## 12.5 Session continuity retrieval

MoonMind may also expose continuity-specific drill-down endpoints for a task-scoped session.

Those endpoints should remain separate from Live Logs transport and should serve durable continuity artifacts such as:
- latest session summary
- latest checkpoint
- control-event history
- reset-boundary history

---

## 13. Data model changes

## 13.1 Managed run record

The managed-run record should treat `stdout`, `stderr`, diagnostics, and session continuity as first-class observability fields.

Suggested shape:

```python
class ManagedRunRecord:
    run_id: str
    agent_id: str
    runtime_id: str
    status: str
    pid: int | None
    exit_code: int | None
    started_at: datetime
    finished_at: datetime | None
    last_heartbeat_at: datetime | None
    workspace_path: str | None

    stdout_artifact_ref: str | None
    stderr_artifact_ref: str | None
    merged_log_artifact_ref: str | None
    diagnostics_ref: str | None

    live_stream_id: str | None
    live_stream_status: str | None
    last_log_offset: int | None
    last_log_at: datetime | None

    session_id: str | None
    session_epoch: int | None
    container_id: str | None
    thread_id: str | None
    active_turn_id: str | None

    latest_session_summary_ref: str | None
    latest_session_checkpoint_ref: str | None
    latest_control_event_ref: str | None
    latest_reset_boundary_ref: str | None
    observability_events_ref: str | None

    error_message: str | None
    failure_class: str | None
```

This is a bounded summary record, not the full event history.

## 13.2 Structured observability history

MoonMind should durably persist structured observability history for managed runs.

This may be implemented as:
- an append-only JSONL artifact
- a DB-backed event table
- another MoonMind-owned durable event journal

Requirements:
- ordered by run-global `sequence`
- supports bounded historical reads
- supports reconstruction after live-stream loss or process restart
- can coexist with the shared spool transport used for active live streaming

## 13.3 Legacy live-session model and deprecation rule

The persisted `TaskRunLiveSession` row and terminal-relay metadata are **legacy compatibility only** for historical runs created before the MoonMind-native observability model existed.

Deprecation rules:
- managed-run observability for new runs must not depend on `attachRo`, `webRo`, socket paths, or terminal-relay metadata
- the deprecated `/api/task-runs/{taskRunId}/live-session*` route family is not part of the supported managed-run observability surface
- historical runs that only persisted legacy `logArtifactRef` should degrade through read-only merged-log fallback rather than through terminal relays
- any code path that reads terminal-session fields for managed-run log viewing is a migration target, not a supported architecture path

---

## 14. Mission Control UI behavior

## 14.1 Default collapsed state

The Live Logs panel should default to collapsed with no active stream.

**No live-stream connection is made while the panel is collapsed.**

## 14.2 On open

When the panel opens:
1. fetch observability summary
2. fetch a structured observability tail when available
3. otherwise fetch the merged tail
4. render initial visible content immediately
5. if streaming is available and the run is active, connect to the live stream
6. show loading state only during initial fetch/connect

The UI must never depend on SSE success before showing initial content.

If the summary indicates `supports_live_streaming: false` or `live_stream_status: ended`, skip the live-connection step entirely.

## 14.3 Header snapshot

The panel header should show the latest known session snapshot when available:
- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

This snapshot is for orientation only.

It must not create the impression that container-local state is authoritative.

## 14.4 States

Recommended viewer states:
- `not_available` — no artifacts and no live stream; run may be starting or pre-launch
- `starting` — summary or initial tail is loading
- `live` — connected to active live stream and receiving events
- `ended` — run is terminal; showing artifact-backed or durable-history content only
- `error` — live-stream connection failed or initial fetch failed; the viewer transitions to fallback content

Allowed state transitions:
- `not_available` → `starting`
- `starting` → `live`
- `starting` → `ended`
- `starting` → `error`
- `live` → `ended`
- `live` → `error`
- `error` → `live`
- `error` → `ended`

## 14.5 Row rendering rules

The Live Logs UI should:
- render output rows as ordinary log lines
- render epoch boundaries as explicit banners or separators
- render approval rows distinctly from raw output
- preserve chronological order by `sequence`
- preserve row provenance so operators can tell whether a line came from `stdout`, `stderr`, `system`, or a session event
- support wrap toggle, copy, and download affordances

## 14.6 Ended runs

If the run is complete:
- do not attempt live streaming
- show the final artifact-backed or durable-history tail
- keep Stdout, Stderr, Diagnostics, and continuity drill-down surfaces available

## 14.7 Panel lifecycle rules

- **collapsed**: no connection, no background streaming
- **open + active run**: fetch initial content, then connect stream
- **open + ended run**: fetch initial content only; never connect stream
- **collapse**: disconnect immediately
- **background tab**: disconnect or pause; reconnect on foreground only if the panel is open and the run is still active
- **stream error**: transition to fallback content; do not leave the panel blank

## 14.8 Feature flag

The observability panel may be disabled through `logStreamingEnabled`, but the default posture is enabled.

Current runtime env/config name:
- `MOONMIND_LOG_STREAMING_ENABLED`

Default:
- `true`

A separate UI flag may be used if a staged rollout of the richer session-aware timeline is required.

---

## 15. Intervention separation

The previous live-task-management model blended live output and terminal handoff under shared session infrastructure.

That should remain split.

## 15.1 Logs panel

Passive only.

## 15.2 Session continuity panel

Read-only continuity artifacts and summaries.

## 15.3 Intervention panel

Explicit controls only:
- Pause
- Resume
- Send operator message
- Approve / Reject
- Cancel
- Request clarification
- Clear session
- Interrupt turn
- Terminate session

These should map to workflow signals, updates, or provider-native control channels, not to terminal access.

## 15.4 Debug-only session

If MoonMind later supports a debug session for managed runs, it must be:
- opt-in
- secondary
- capability-gated
- never the source of truth for logs, continuity, or diagnosis

---

## 16. Updated requirements

### Functional requirements

- **FR-001**: The task detail page must include a collapsible Live Logs panel.
- **FR-002**: The Live Logs panel must fetch initial artifact-backed or durable-history content from MoonMind APIs. Initial content must not depend on SSE success.
- **FR-003**: When the run is active and live streaming is supported, the panel must connect to a MoonMind-owned live stream.
- **FR-004**: The panel must default to collapsed with no active connection.
- **FR-005**: The panel must disconnect when collapsed.
- **FR-006**: The panel must disconnect or pause when the browser tab loses visibility.
- **FR-007**: The panel must reconnect when reopened or when visibility returns, only if the run is still active.
- **FR-008**: The system must preserve `stdout`, `stderr`, diagnostics, and continuity artifacts for every managed run.
- **FR-009**: The UI must provide separate Stdout, Stderr, and Diagnostics views.
- **FR-010**: The Live Logs viewer must not depend on tmate, `web_ro`, or terminal embedding.
- **FR-011**: The feature must support an operator-visible disable switch such as `logStreamingEnabled`, with the default configuration enabled.
- **FR-012**: The observability system must continue to function when live streaming is unavailable by falling back to durable retrieval.
- **FR-013**: The launcher must not wrap managed agent runs in tmate for log visibility.
- **FR-014**: Logging and intervention must be modeled separately in both backend APIs and Mission Control UI.
- **FR-015**: The live-stream transport must be cross-process; an API-local in-memory singleton is not compliant.
- **FR-016**: Ended runs must never trigger a live-stream connection attempt.
- **FR-017**: Stream errors must transition the viewer to fallback mode, not leave the panel blank.
- **FR-018**: The observability summary must expose the latest bounded session snapshot when one exists.
- **FR-019**: Live Logs must render session epoch boundaries explicitly.
- **FR-020**: `clear_session` must publish observable boundary information through both artifacts and the live or durable timeline.
- **FR-021**: The merged observability timeline must support `stdout`, `stderr`, `system`, and session events in one run-global sequence namespace.
- **FR-022**: Historical operator presentation must not depend on in-memory container state, container-local thread databases, terminal scrollback, or runtime home directories.
- **FR-023**: The browser-facing contract must be MoonMind-normalized rather than a raw provider-native event stream.
- **FR-024**: The system must support backward-compatible degradation for historical runs that only have merged text or legacy artifact refs.
- **FR-025**: Approval-required states must be visible in observability without forcing operators into a separate terminal-oriented workflow.

### Key entities

- **Managed Run Record**
- **Task Run Observability Session**
- **Run Observability Event**
- **Structured Event Journal**
- **Live Log Stream**
- **Stdout Artifact**
- **Stderr Artifact**
- **Diagnostics Artifact**
- **Session Summary Artifact**
- **Session Checkpoint Artifact**
- **Session Control Event Artifact**
- **Session Reset Boundary Artifact**
- **Intervention Capability Set**

---

## 17. Success criteria

- **SC-001**: Operators can see the latest visible observability tail within 2 seconds of opening the Live Logs panel.
- **SC-002**: Operators can receive live updates for active runs without embedding a terminal viewer.
- **SC-003**: Every managed run produces durable `stdout`, `stderr`, diagnostics, and continuity outputs even if no UI was connected.
- **SC-004**: Closing the panel or backgrounding the tab stops live streaming within a few seconds.
- **SC-005**: Ended runs still show useful logs, diagnostics, and continuity boundaries without requiring any saved terminal transcript.
- **SC-006**: Managed-run observability works identically whether or not any terminal transport exists elsewhere in the system.
- **SC-007**: Stream errors do not erase visible history; the panel degrades to durable retrieval.
- **SC-008**: Mission Control can fully observe a completed run without having ever had a live-stream connection.
- **SC-009**: Operators can clearly see when a session epoch changed and why.
- **SC-010**: `clear_session` creates an explicit, operator-visible epoch boundary in both historical and live views.
- **SC-011**: A refreshed task detail page can reconstruct the latest session-aware observability view from durable artifacts and bounded metadata alone.

---

## 18. Implementation tracking

Phased rollout notes, migration sequencing, and remaining implementation work are tracked in [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md) so this document stays focused on the target-state contract.

---

## 19. Bottom line

With the updated decision:
- **OAuth terminal UX** should use `xterm.js`
- **managed run logs** should not
- **Codex managed session continuity** must become visible inside the same MoonMind observability experience

The architecture should move from:
- terminal embedding
- tmate endpoints
- session-viewer semantics
- line-only live output assumptions

To:
- artifact-backed logs
- MoonMind-owned observability APIs
- a normalized session-aware event model
- optional live streaming via a cross-process shared transport
- explicit continuity artifacts and epoch boundaries
- separate intervention controls

That is the cleanest architecture and the one that best matches both the existing Live Logs direction and the Codex managed session plane.
