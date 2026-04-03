# Live Logs and Run Observability in MoonMind

**Replaces or substantially rewrites:**

* `docs/Temporal/LiveTaskManagement.md`
* `specs/084-live-log-tailing/spec.md`

## 1. Summary

MoonMind should stop treating live logs as an embedded terminal session.

For managed agent runs, live visibility should be based on:

* durable stdout/stderr artifact capture
* structured diagnostics
* optional MoonMind-owned live log streaming to Mission Control
* explicit intervention controls separate from logging

With this design:

* **OAuth** uses `xterm.js` plus a MoonMind PTY/WebSocket bridge because OAuth is an interactive terminal flow
* **managed run logs** do **not** use `xterm.js` and do **not** use terminal embedding
* Mission Control shows logs through a MoonMind-native React log viewer backed by MoonMind APIs and artifacts, implemented with `react-virtuoso` for virtualized rendering, `anser` for ANSI parsing, TanStack Query for retrieval state, and SSE via `EventSource` for live follow mode.

This keeps logging deterministic, persistent, auditable, and independent from interactive terminal transport.

### 1.1 Implementation tracking

This document is the canonical desired-state architecture for managed-run observability.

Current implementation status, phased rollout notes, and remaining migration work live in [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md).

---

## 2. Why the old design no longer fits

The old design assumed:

* every managed run is wrapped in tmate
* live logs come from `tmate web_ro`
* the task detail page embeds a read-only terminal viewer
* no new backend log API is needed

That model conflicts with the new direction in two ways.

First, tmate is being removed from managed execution. The launcher should no longer decide observability behavior based on whether tmate is installed .

Second, OAuth and logging are different products:

* OAuth needs a real interactive browser terminal
* logs need a reliable observability surface with durable capture, tails, searchability, and postmortem diagnostics

A terminal emulator is the wrong primary UI for logs.

---

## 3. Core decision

MoonMind should adopt this rule:

**Managed run observability is artifact-first and MoonMind-owned.**

Every managed run should produce:

* durable stdout artifact
* durable stderr artifact
* durable diagnostics artifact
* structured run state and summary metadata
* optional live streaming through MoonMind APIs

**Logging is not intervention.**
The live logs UI is for passive observation. Intervention is a separate control surface.

**`xterm.js` is reserved for OAuth sessions, not for managed run logs.**

---

## 4. Goals

### Primary goals

The live logs system should:

* work even when no interactive session exists
* always preserve logs and diagnostics after the run ends
* support live observation for running tasks
* keep the task detail page simple and operator-friendly
* survive reconnects, refreshes, and temporary disconnects
* avoid dependence on external session transports

### Non-goals

This system does not aim to:

* provide shell access for ordinary task runs
* reproduce raw terminal escape semantics perfectly
* replace provider-native intervention channels
* make logs depend on browser attachment
* implement distributed full-text log indexing or search in the first version
* implement a terminal-grade durable replay buffer for every transient live event

---

## 5. User experience

## 5.1 Task detail page

The task detail page should include an **Observability** section with these tabs or panels:

* **Live Logs**
* **Stdout**
* **Stderr**
* **Diagnostics**
* **Artifacts**

The default operator workflow should be:

1. open task
2. see status and summary
3. open Live Logs for real-time observation
4. inspect Stdout / Stderr / Diagnostics as needed
5. use separate Intervention controls if action is required

## 5.2 Live Logs panel

The Live Logs panel should be a MoonMind-native log viewer, not an embedded terminal.

Behavior:

* initially loads the most recent tail from MoonMind APIs — **initial visible content must not depend on SSE success**
* upgrades to a live stream when the run is active and live streaming is supported
* shows stream origin per line or chunk (`stdout`, `stderr`, `system`)
* can reconnect from last known sequence or offset — resume is best-effort; artifacts are the durable fallback
* falls back to artifact tail if live streaming is unavailable — stream errors must transition to artifact-backed mode rather than leaving the panel blank
* stops streaming when collapsed or when the tab is backgrounded
* **ended runs never attempt live stream connection**; the final artifact-backed tail is always available

## 5.3 Stdout and Stderr panels

These are artifact-backed viewers for the individual streams.

They should support:

* tail view
* full download
* simple copy
* line wrapping toggle
* light filtering or search later

## 5.4 Diagnostics panel

Diagnostics should show structured metadata such as:

* run status
* exit code
* duration
* failure class
* timestamps
* parsed errors or warnings
* rate-limit detection
* artifact refs
* summary

The current supervisor/log streamer already writes diagnostics JSON and persists refs, so this panel builds on that existing direction rather than inventing a new system .

---

## 6. Architectural model

## 6.1 Observation plane

The observation plane owns passive visibility.

Components:

* launcher
* supervisor
* log capture pipeline
* diagnostics builder
* live log stream publisher
* Mission Control log APIs
* artifact-backed log retrieval

## 6.2 Control plane

The control plane owns intervention.

Components:

* Temporal signals and updates
* provider-native messaging/control adapters
* audit trail for intervention actions

## 6.3 OAuth plane

OAuth uses a different transport because it is interactive.

Components:

* `xterm.js`
* OAuth PTY/WebSocket bridge
* short-lived auth container

That plane is explicitly separate from the log viewer.

## 6.4 Cross-process transport boundary

**Critical constraint:** The live stream publisher must be cross-process or otherwise shared across the producer and API/UI boundary.

An API-process-local in-memory publisher is **not** sufficient as the canonical implementation model.

The design must support the fact that managed runtime supervision and API serving may live in different processes or containers.

Rules:

* "The live stream transport must not assume the log producer runs in the same API process."
* "A process-local replay buffer may exist as a performance optimization, but it is not the architecture boundary."
* Live publication must target a shared MoonMind observability transport — such as Redis pub/sub, a shared append-only spool, or DB-backed tailing — not an API-local singleton in memory.
* Current implementation choice: a shared append-only spool file under the run workspace is the active transport boundary between the runtime supervisor and the API SSE reader.

---

## 7. Runtime contract

Every managed run must produce these durable outputs.

## 7.1 Required outputs

* `stdout_artifact_ref`
* `stderr_artifact_ref`
* `diagnostics_ref`
* optional `merged_log_artifact_ref` — may be absent; see section 7.3 for merged-tail behavior when it is missing
* structured run summary metadata

## 7.2 Optional live observability outputs

* `live_stream_id`
* `live_stream_status`
* `last_log_offset`
* `last_log_at`
* `supports_live_streaming`

These fields are non-authoritative convenience metadata. Artifacts remain authoritative.

For current managed runs, `supports_live_streaming` is derived from whether the launcher persisted a real spool-backed live-stream capability for that run. Terminal runs still suppress live connectivity even if they were stream-capable while active.

## 7.3 Merged-tail contract

The merged-tail endpoint (`/logs/merged`) may return content computed in one of two ways:

* from a pre-built `merged_log_artifact_ref` if one exists
* synthesized on demand from `stdout_artifact_ref` + `stderr_artifact_ref` + spool/supervisor metadata if no merged artifact exists

Rules for merged tail:

* live log `sequence` is one run-global monotonically increasing namespace shared across `stdout`, `stderr`, and `system`
* when spool metadata is available, merged content is ordered by monotonically increasing `sequence` value assigned at emit time
* system events (supervisor annotations, reconnect notices, truncation warnings) may appear in the merged tail and must be labeled with `stream: "system"`
* `merged_log_artifact_ref` is **optional**; implementations must not require it to exist in order to serve a merged tail
* the endpoint must handle partial artifacts and still return whatever durably captured content is available
* when a historical run lacks both `merged_log_artifact_ref` and spool metadata, the endpoint may fall back to labeled stdout/stderr artifact concatenation with an explicit warning that chronological merge order is unavailable

## 7.4 Supervisor Annotations

Supervisor annotations are MoonMind-owned `system` events emitted from the runtime supervision / observability layer.

They are explicitly separate from raw `stdout` and `stderr` artifacts:

* they represent MoonMind-observed facts (lifecycle, supervision decisions, fallback behavior)
* they must never rewrite or paraphrase subprocess output
* they may appear in merged tails with `stream="system"` using the shared run-global sequence
* they are not a substitute for provider-native structured output when that later becomes available

### When supervisor annotations are recommended

Emit `system` annotations when MoonMind can add high-value operator context, especially where raw output is:

* sparse or delayed
* repetitive
* ambiguous
* degraded by fallback behavior

Good use cases:

* run lifecycle transitions (`run_started`, `command_launched`)
* workspace preparation outcomes (`workspace_preparation_applied`, `workspace_preparation_skipped`)
* first-output visibility (`first_stdout_seen`, `first_stderr_seen`)
* inactivity and fallback behavior (`no_output_interval`, `live_stream_unavailable`, `artifact_fallback`, `chronological_merge_unavailable`)
* repeated warning deduplication (`warning_deduplicated`)
* supervision decisions (`termination_requested_timeout`, `termination_requested_cancel`, `termination_requested_rate_limit`)
* final classification and persistence (`exit_code_resolved`, `run_classified_completed`, `run_classified_failed`, `run_classified_timed_out`, `diagnostics_written`)

### When supervisor annotations are not recommended

Do not use `system` annotations to:

* mirror parent-workflow status prints like “doing X / now doing Y”
* simulate model reasoning
* paraphrase normal `stdout`/`stderr` lines
* spam progress updates without diagnostic value
* provide low-signal heartbeat messages with no operational meaning

### Example events that should receive supervisor annotations

* `run_started` — `Supervisor: managed run started.`
* `workspace_preparation_applied` — `Supervisor: workspace instructions written to CLAUDE.md.`
* `workspace_preparation_skipped` — `Supervisor: skipped writing CLAUDE.md because the file already exists or is a symlink.`
* `command_launched` — `Supervisor: runtime command launched in managed mode.`
* `first_stdout_seen` — `Supervisor: first stdout output received.`
* `first_stderr_seen` — `Supervisor: first stderr output received.`
* `no_output_interval` — `Supervisor: no stdout/stderr observed for 30s; process still running.`
* `warning_deduplicated` — `Supervisor: repeated config warning observed 12 times; suppressing duplicates in live view.`
* `live_stream_unavailable` — `Supervisor: live streaming unavailable; durable artifact capture continues.`
* `artifact_fallback` — `Supervisor: merged log view is using artifact-backed fallback.`
* `chronological_merge_unavailable` — `Supervisor: chronological merged ordering unavailable; showing labeled concatenation fallback.`
* `termination_requested_timeout` — `Supervisor: process termination requested after timeout.`
* `termination_requested_cancel` — `Supervisor: process termination requested due to operator cancel.`
* `termination_requested_rate_limit` — `Supervisor: process termination requested due to live rate-limit detection.`
* `exit_code_resolved` — `Supervisor: authoritative exit code resolved to 1.`
* `run_classified_completed` — `Supervisor: run classified as completed.`
* `run_classified_failed` — `Supervisor: run classified as failed (execution_error).`
* `run_classified_timed_out` — `Supervisor: run classified as timed_out.`
* `diagnostics_written` — `Supervisor: diagnostics bundle persisted.`

---

## 8. Runtime execution model

## 8.1 Launcher

The launcher must always start managed agent runs as ordinary subprocesses with:

* `stdout=PIPE`
* `stderr=PIPE`

It must not wrap the normal managed path in tmate.

## 8.2 Supervisor

The supervisor owns:

* concurrent stdout/stderr draining
* heartbeats
* timeout handling
* exit classification
* diagnostics generation
* final state persistence
* emission of live log records for active subscribers
* updating `last_log_at` and `last_log_offset` metadata used by the observability summary
* generation of `system` event annotations where needed (e.g. run start, truncation notices)
* handoff of live log chunks to the shared live-stream transport boundary

**Durability comes first. Live stream publication is secondary. Live publication failure must not break artifact persistence or run completion.**

This fits the existing managed-agent execution model much better than terminal embedding, because that model already says logs and diagnostics should be artifact-backed and that runtime events should map into workflow-native mechanisms .

## 8.3 Log capture pipeline

For each chunk read from stdout or stderr:

1. append to durable storage or spool
2. update artifact-building state
3. optionally publish to live subscribers via the shared transport boundary
4. update stream metadata (`last_log_at`, `last_log_offset`)

Durability comes first. Live streaming is secondary.

MoonMind-generated system events should use `structlog`, but managed-agent `stdout` and `stderr` remain raw subprocess streams and must not be normalized into framework logs before artifact persistence.

---

## 9.0 Selected implementation baseline

The phrase "MoonMind-native log viewer" is intentionally **not** a terminal emulator and **not** a third-party hosted logging product. It means a MoonMind-owned UI and API surface built on a small number of explicit libraries.

### Backend logging and event tools

MoonMind should standardize on:

- **`structlog`** for MoonMind-generated structured events
- **direct subprocess pipe capture** for managed-agent `stdout` and `stderr`
- **OpenTelemetry** for trace and metric correlation
- **FastAPI / Starlette SSE streaming** for live log delivery

#### Rule: `stdout` and `stderr` are not `structlog`

Managed-agent output must remain direct pipe capture from the launched process.

MoonMind must not transform managed-agent `stdout` / `stderr` into framework-owned application logs before persistence, because doing so risks:

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
- system annotations injected into the merged stream

### Frontend viewer foundation

The Mission Control Live Logs panel should be implemented as a native React component using:

- **`react-virtuoso`** for virtualized rendering of long and growing log streams
- **`anser`** for ANSI parsing into styled spans
- **browser `EventSource`** for SSE live follow mode
- **TanStack Query** for initial tail fetch, fallback retrieval, and cache invalidation

This combination is preferred over terminal or editor widgets because the Live Logs panel is a passive observability surface, not an interactive shell and not a code editor.

### Why this baseline was selected

Implementation tracking note:

* the preferred rendering foundation remains TanStack Query plus `EventSource` for transport, with `react-virtuoso` and `anser` as the desired viewer baseline
* concrete rollout status for the current viewer implementation is tracked in [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md)

#### Why `react-virtuoso`

`react-virtuoso` is the preferred rendering base because log viewers have requirements that differ from ordinary tables:

- thousands of rows
- continuously appended content
- variable line heights when wrapping is enabled
- jump-to-bottom and follow-tail behavior
- smooth rendering without mounting the entire log

A generic `<pre>` element is acceptable for very small logs but should not be the implementation basis for MoonMind.

#### Why `anser`

Managed runtimes may emit ANSI color and formatting codes. MoonMind should support a useful subset of ANSI formatting in the viewer without embedding a full terminal emulator.

`anser` should be used to parse ANSI sequences into structured segments that React renders as spans. The viewer should not rely on raw terminal escape replay.

#### Why not `xterm.js`

`xterm.js` remains the correct choice for OAuth sessions because OAuth is interactive. It is not the correct basis for the Live Logs panel because:

- logs are passive
- logs must be artifact-first
- logs need line identity and stream provenance
- logs should not inherit terminal transport semantics

#### Why not Monaco / CodeMirror

Editor widgets are too heavy and introduce the wrong mental model. MoonMind needs:

- a tailing stream surface
- stream origin badges
- reconnect behavior
- artifact fallback
- diagnostics integration

It does not need editing, buffers, or editor commands.

### SSE implementation guidance

The first implementation should use SSE over `text/event-stream`.

MoonMind should not add a WebSocket dependency for the first version of live logs unless later requirements demand bidirectional behavior.

The server may use a small internal SSE encoder helper. A dedicated SSE framework dependency is optional, not required.

### Rendering contract

The API remains authoritative for raw text. The UI is responsible for presentation.

Each live or fetched log record should contain:

- `sequence`
- `stream` (`stdout`, `stderr`, or `system`)
- `offset`
- `timestamp`
- `text`

Optional presentation hints may be added later, but the backend should not send pre-rendered HTML log fragments.

### Viewer capability requirements

The selected frontend implementation must support:

- follow-tail mode
- reconnect from last sequence
- line wrapping toggle
- copy selected lines
- per-line stream provenance
- artifact-backed initial load
- artifact fallback when live streaming is unavailable
- future inline annotations for intervention or supervisor events

---

## 9. Live streaming design

## 9.1 Transport

Use MoonMind-owned streaming.

Recommended first version:

* **Server-Sent Events** for simple operator consumption

Possible later version:

* WebSocket if richer interactive behaviors are needed

SSE is enough for one-way live logs.

## 9.2 Stream endpoint

Canonical endpoint:

`GET /api/task-runs/{id}/logs/stream`

Optional query params:

* `merged=true`
* `since=<sequence_or_offset>`
* `streams=stdout,stderr,system`

Example event payload:

```json id="5q6ein"
{
  "runId": "run_123",
  "sequence": 42,
  "stream": "stdout",
  "offset": 12288,
  "timestamp": "2026-03-28T20:14:03Z",
  "text": "Running unit tests...\n"
}
```

Expected HTTP behavior:

* **active run, streaming available**: respond with `200 text/event-stream` and stream events
* **active run, streaming not supported**: respond with `200` and an appropriate status event; caller falls back to artifact-backed polling
* **ended run**: respond with `200` and a single terminal event or an empty stream indicating `ended`; caller must not reconnect
* **artifacts missing or partial**: the stream endpoint returns whatever is available; the observability summary indicates artifact status
* **stream unavailable**: respond with `503` or an error event; caller transitions to artifact-backed mode

## 9.3 Reconnect behavior

Mission Control should reconnect using the last known sequence or offset.

For the live SSE path, the canonical resume cursor is the run-global `sequence` value shared across all streams.

**Resume is best-effort.** The system is not required to durably preserve every transient live event in a stream-only buffer.

* resume works while the live stream backend still retains the in-memory or short-lived replay window
* if the resume window has expired or the stream backend has restarted, resume falls back to durable artifact retrieval
* artifacts are the authoritative durable fallback; they define what happened

If the live stream cannot resume:

* fetch a merged artifact-backed tail
* render the latest lines
* re-enter live mode if the stream is still active

## 9.4 Background tab behavior

When the tab is backgrounded or the panel is collapsed:

* disconnect the live stream
* do not continue background streaming
* reconnect only if the panel is open and the run is still active

This preserves the useful part of the old behavior without depending on tmate viewer embedding.

---

## 10. Artifact-backed log retrieval APIs

These are now required. The old design explicitly said no new backend API was needed because `web_ro` did the work . That is no longer true.

## 10.1 Observability summary

`GET /api/task-runs/{id}/observability-summary`

Minimum required response fields:

* `run_id`
* `status` — canonical run status
* `stdout_artifact_ref` — nullable
* `stderr_artifact_ref` — nullable
* `diagnostics_ref` — nullable
* `merged_log_artifact_ref` — nullable
* `supports_live_streaming` — boolean
* `live_stream_id` — nullable
* `live_stream_status` — nullable (`available`, `ended`, `unavailable`)
* `last_log_at` — nullable timestamp
* `last_log_offset` — nullable int
* `intervention_capabilities` — summary of available controls

Behavior when the run is terminal:

* `live_stream_status` must be `ended` or `unavailable`
* `supports_live_streaming` must be `false` or the API must clearly signal that no stream connection is appropriate
* artifact refs remain populated and queryable indefinitely

Behavior when live streaming is unsupported:

* `supports_live_streaming: false`
* UI falls through to artifact-backed retrieval without attempting SSE

Behavior when artifacts are missing or partial:

* refs are `null`; the UI shows whatever is available
* partial artifacts should still be retrievable via the tail endpoints

## 10.2 Tail endpoints

* `GET /api/task-runs/{id}/logs/stdout`
* `GET /api/task-runs/{id}/logs/stderr`
* `GET /api/task-runs/{id}/logs/merged`

Each endpoint must handle:

* ended runs: return final artifact tail, no stream connection
* missing artifacts: return empty body or appropriate 404/empty response
* partial artifacts: return whatever has been durably captured so far

## 10.3 Full retrieval / download

* `GET /api/task-runs/{id}/logs/stdout/download`
* `GET /api/task-runs/{id}/logs/stderr/download`
* `GET /api/task-runs/{id}/diagnostics`

---

## 11. Data model changes

## 11.1 Managed run record

The current managed run model only exposes one generic `log_artifact_ref` plus `diagnostics_ref` in the runtime contract direction you reviewed earlier. The updated design should treat stdout and stderr as first-class fields.

Suggested shape:

```python id="muj31r"
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

    error_message: str | None
    failure_class: str | None
```

## 11.2 Legacy live-session model and deprecation rule

The persisted `TaskRunLiveSession` row uses `provider` (e.g. `none`), `liveSessionName`, `liveSessionSocketPath`, `attachRo`, and `webRo` where workers report relay metadata.

**Deprecation rule:**

* `TaskRunLiveSession` is **legacy compatibility only** for historical runs that were created before the MoonMind-native observability model existed.
* managed-run observability for new runs must **not** depend on `attachRo`, `webRo`, socket paths, or terminal-relay metadata.
* the deprecated `/api/task-runs/{taskRunId}/live-session*` route family is **not** part of the supported managed-run observability API surface; any remaining references are migration-only.
* new managed runs must use observability-specific metadata only (`stdout_artifact_ref`, `stderr_artifact_ref`, `live_stream_id`, etc.).
* historical runs that only persisted `logArtifactRef` should degrade through read-only merged-log fallback, not through terminal relays.
* any code path that reads terminal-session fields for managed-run log viewing is a migration target, not a supported architecture path.

Recommended replacement model:

### `task_run_observability_sessions`

Fields:

* `task_run_id`
* `provider = "moonmind_logs"`
* `status`
* `live_stream_id`
* `stdout_artifact_ref`
* `stderr_artifact_ref`
* `diagnostics_ref`
* `last_log_at`
* `last_log_offset`
* `last_heartbeat_at`
* `supports_live_streaming`
* `error_message`

This is much closer to the actual problem being solved.

---

## 12. Mission Control UI behavior

## 12.1 Default collapsed state

The Live Logs panel should default to collapsed with no active stream.

**No live stream connection is made while the panel is collapsed.**

## 12.2 On open

When the panel opens:

1. fetch observability summary
2. fetch merged tail — **initial content must be visible without waiting for SSE**
3. if stream is available and run is active, connect to stream
4. show loading state only during initial fetch/connect

The UI must never depend on SSE success before showing initial log content. If the observability summary indicates `supports_live_streaming: false` or `live_stream_status: ended`, skip step 3 entirely.

## 12.3 States

Recommended UI states:

* `not_available` — no artifacts and no live stream; run may be starting or pre-launch
* `starting` — observability summary is loading or initial tail is in flight
* `live` — connected to active live stream; receiving events
* `ended` — run is terminal; showing artifact-backed tail only
* `error` — live stream connection failed; transitioned to artifact-backed mode

Allowed state transitions:

* `not_available` → `starting`
* `starting` → `live` (run active, stream available)
* `starting` → `ended` (run already terminal)
* `starting` → `error` (initial fetch failed)
* `live` → `ended` (run completes)
* `live` → `error` (stream connection fails)
* `error` → `live` (reconnect succeeds and run still active)
* `error` → `ended` (reconnect not attempted; run is terminal)

## 12.4 Ended runs

If the run is complete:

* do not attempt live stream
* show the final artifact-backed tail
* keep Stdout / Stderr / Diagnostics panels available

This is much better than the old "Session ended" with no stream and maybe a transcript artifact, because the new system always has durable logs by design .

## 12.5 Panel lifecycle rules

* **collapsed**: no connection, no background streaming
* **open + active run**: fetch tail → connect stream
* **open + ended run**: fetch tail only; never connect stream
* **collapse**: disconnect immediately
* **background tab**: disconnect or pause; reconnect on foreground only if panel is open and run is still active
* **stream error**: transition to `error` state and show artifact-backed content; do not leave panel blank

## 12.6 Feature flag

The observability panel may be disabled through `logStreamingEnabled`, but the default posture is enabled.

Current runtime env/config name: `MOONMIND_LOG_STREAMING_ENABLED`.
Default: `true`.

A separate UI flag for the new observability panel layout may be used if a side-by-side rollout with the legacy view is required.

---

## 13. Intervention separation

The old live-task management doc blended live output and terminal handoff under shared tmate session infrastructure . That should be split.

## 13.1 Logs panel

Passive only.

## 13.2 Intervention panel

Explicit controls only:

* Pause
* Resume
* Send operator message
* Approve / Reject
* Cancel
* Request clarification

These should map to workflow signals/updates or provider-native channels, not terminal access.

## 13.3 Debug-only session

If MoonMind later decides to support a debug session for managed runs, it should be:

* opt-in
* secondary
* capability-gated
* never the source of truth for logs or diagnosis

---

## 14. Updated requirements

This is the direct replacement for the old tmate-oriented live-log requirements.

### Functional requirements

* **FR-001**: The task detail page must include a collapsible Live Logs panel.
* **FR-002**: The Live Logs panel must fetch an initial artifact-backed merged tail from MoonMind APIs. Initial content must not depend on SSE success.
* **FR-003**: When the run is active and live streaming is supported, the panel must connect to a MoonMind-owned live log stream.
* **FR-004**: The panel must default to collapsed with no active connection.
* **FR-005**: The panel must disconnect when collapsed.
* **FR-006**: The panel must disconnect or pause when the browser tab loses visibility.
* **FR-007**: The panel must reconnect when reopened or when visibility returns, only if the run is still active.
* **FR-008**: The system must preserve stdout, stderr, and diagnostics as durable artifacts for every managed run.
* **FR-009**: The UI must provide separate Stdout, Stderr, and Diagnostics views.
* **FR-010**: The live log viewer must not depend on tmate, `web_ro`, or terminal embedding.
* **FR-011**: The feature must support an operator-visible disable switch such as `logStreamingEnabled`, with the default configuration enabled.
* **FR-012**: The observability system must continue to function when live streaming is unavailable by falling back to artifact-backed retrieval.
* **FR-013**: The launcher must not wrap managed agent runs in tmate for log visibility.
* **FR-014**: Logging and intervention must be modeled separately in both backend APIs and Mission Control UI.
* **FR-015**: The live stream transport must be cross-process; an API-local in-memory singleton is not compliant.
* **FR-016**: Ended runs must never trigger a live stream connection attempt.
* **FR-017**: Stream errors must transition the viewer to artifact-backed mode, not leave the panel blank.

### Key entities

* **Managed Run Record**
* **Task Run Observability Session**
* **Live Log Stream**
* **Stdout Artifact**
* **Stderr Artifact**
* **Diagnostics Artifact**
* **Intervention Capability Set**

---

## 15. Success criteria

* **SC-001**: Operators can see the latest artifact-backed log tail within 2 seconds of opening the Live Logs panel.
* **SC-002**: Operators can receive live log updates for active runs without embedding a terminal viewer.
* **SC-003**: Every managed run produces durable stdout, stderr, and diagnostics outputs, even if no UI was connected.
* **SC-004**: Closing the panel or backgrounding the tab stops live streaming within a few seconds.
* **SC-005**: Ended runs still show useful logs and diagnostics without requiring any saved terminal transcript.
* **SC-006**: Managed run logging works identically whether or not any terminal transport exists elsewhere in the system.
* **SC-007**: Stream errors do not erase visible logs; the panel degrades to artifact-backed mode.
* **SC-008**: Mission Control can fully observe a completed run without having ever had a live stream connection.

---

## 16. Implementation tracking

Phased rollout notes, migration sequencing, and remaining implementation work are tracked in [`docs/tmp/009-LiveLogsPlan.md`](../tmp/009-LiveLogsPlan.md) so this document stays focused on the target-state contract.

---

## 17. Bottom line

With the new decision:

* **OAuth terminal UX** should use `xterm.js`
* **managed run logs** should not

The updated logs design should move from:

* terminal embedding
* tmate endpoints
* session-viewer semantics

to:

* artifact-backed logs
* MoonMind log APIs
* optional live streaming via a cross-process shared transport
* separate intervention controls

That is the cleanest architecture and the one that best matches the broader shift away from tmate.
