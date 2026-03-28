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

* initially loads the most recent tail from MoonMind APIs
* upgrades to a live stream when the run is active
* shows stream origin per line or chunk (`stdout`, `stderr`, `system`)
* can reconnect from last known sequence or offset
* can fall back to artifact tail if live streaming is unavailable
* stops streaming when collapsed or when the tab is backgrounded

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

---

## 7. Runtime contract

Every managed run must produce these durable outputs.

## 7.1 Required outputs

* `stdout_artifact_ref`
* `stderr_artifact_ref`
* `diagnostics_ref`
* optional `merged_log_artifact_ref`
* structured run summary metadata

## 7.2 Optional live observability outputs

* `live_stream_id`
* `live_stream_status`
* `last_log_offset`
* `last_log_at`
* `supports_live_streaming`

These fields are non-authoritative convenience metadata. Artifacts remain authoritative.

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
* optional fan-out to live subscribers

This fits the existing managed-agent execution model much better than terminal embedding, because that model already says logs and diagnostics should be artifact-backed and that runtime events should map into workflow-native mechanisms .

## 8.3 Log capture pipeline

For each chunk read from stdout or stderr:

1. append to durable storage or spool
2. update artifact-building state
3. optionally publish to live subscribers
4. update stream metadata

Durability comes first. Live streaming is secondary.

MoonMind-generated system events should use `structlog`, but managed-agent `stdout` and `stderr` remain raw subprocess streams and must not be normalized into framework logs before artifact persistence.

---

## 9.0 Selected implementation baseline

The phrase “MoonMind-native log viewer” is intentionally **not** a terminal emulator and **not** a third-party hosted logging product. It means a MoonMind-owned UI and API surface built on a small number of explicit libraries.

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

Suggested endpoint:

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

## 9.3 Reconnect behavior

Mission Control should reconnect using the last known sequence or offset.

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

`GET /api/task-runs/{id}/observability`

Response should include:

* run status
* stdout ref
* stderr ref
* diagnostics ref
* live stream availability
* last log timestamp
* intervention capability summary

## 10.2 Tail endpoints

* `GET /api/task-runs/{id}/logs/stdout?tail_lines=200`
* `GET /api/task-runs/{id}/logs/stderr?tail_lines=200`
* `GET /api/task-runs/{id}/logs/merged-tail?tail_lines=200`

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

## 11.2 Live-session model

The current `TaskRunLiveSession` schema is tmate-specific, with fields such as `provider=tmate`, `tmateSessionName`, `tmateSocketPath`, `attachRo`, and `webRo` .

That should be replaced or deprecated for managed-run observability.

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

## 12.2 On open

When the panel opens:

1. fetch observability summary
2. fetch merged tail
3. if stream is available and run is active, connect to stream
4. show loading state only during initial fetch/connect

## 12.3 States

Recommended UI states:

* `not_available`
* `starting`
* `live`
* `ended`
* `error`

These are log-view states, not terminal-transport states.

## 12.4 Ended runs

If the run is complete:

* do not attempt live stream
* show the final artifact-backed tail
* keep Stdout / Stderr / Diagnostics panels available

This is much better than the old “Session ended” with no stream and maybe a transcript artifact, because the new system always has durable logs by design .

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
* **FR-002**: The Live Logs panel must fetch an initial artifact-backed merged tail from MoonMind APIs.
* **FR-003**: When the run is active and live streaming is supported, the panel must connect to a MoonMind-owned live log stream.
* **FR-004**: The panel must default to collapsed with no active connection.
* **FR-005**: The panel must disconnect when collapsed.
* **FR-006**: The panel must disconnect or pause when the browser tab loses visibility.
* **FR-007**: The panel must reconnect when reopened or when visibility returns.
* **FR-008**: The system must preserve stdout, stderr, and diagnostics as durable artifacts for every managed run.
* **FR-009**: The UI must provide separate Stdout, Stderr, and Diagnostics views.
* **FR-010**: The live log viewer must not depend on tmate, `web_ro`, or terminal embedding.
* **FR-011**: The feature must be gated behind a feature flag such as `logStreamingEnabled`.
* **FR-012**: The observability system must continue to function when live streaming is unavailable by falling back to artifact-backed retrieval.
* **FR-013**: The launcher must not wrap managed agent runs in tmate for log visibility.
* **FR-014**: Logging and intervention must be modeled separately in both backend APIs and Mission Control UI.

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

---

## 16. Migration plan

## Phase 1

Rewrite the docs and specs to remove `tmate web_ro` and terminal embedding requirements.

## Phase 2

Change launcher/supervisor contracts so managed runs always use direct subprocess pipes.

## Phase 3

Add observability APIs:

* summary
* merged tail
* stdout/stderr retrieval
* diagnostics
* live stream

## Phase 4

Replace Mission Control Live Output terminal embed with MoonMind-native log viewer.

## Phase 5

Deprecate or replace `TaskRunLiveSession` for managed-run observability.

## Phase 6

Keep `xterm.js` only in the OAuth subsystem.

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
* optional live streaming
* separate intervention controls

That is the cleanest architecture and the one that best matches the broader shift away from tmate.
