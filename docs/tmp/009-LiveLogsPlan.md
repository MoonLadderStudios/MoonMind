# Live Logs Implementation Plan

This document turns `docs/ManagedAgents/LiveLogs.md` into a phased implementation plan for MoonMind.

## Objective

Implement MoonMind-managed live logs for managed agent runs using an artifact-first model with:

- durable `stdout`, `stderr`, and diagnostics artifacts
- MoonMind-owned observability APIs
- optional live streaming for active runs
- a Mission Control log viewer for passive observation
- explicit separation between logs, intervention controls, and OAuth terminal flows

## Guiding decisions

- Managed run logs are **not** terminal sessions.
- `xterm.js` is reserved for **OAuth** and other interactive auth flows, not run logs.
- Managed runs must always capture raw `stdout` and `stderr` directly from subprocess pipes.
- Durable artifacts are the source of truth; live streaming is a convenience layer.
- Logging and intervention must be modeled separately in the backend and UI.
- Legacy `tmate`, `web_ro`, and terminal-embed assumptions must be removed from the managed-run log path.

## Scope

This plan covers:

- managed runtime launcher and supervisor changes
- data model and persistence updates
- artifact production and retrieval APIs
- live log streaming
- Mission Control UI work
- migration away from `TaskRunLiveSession`-style terminal-session assumptions for observability
- rollout, testing, and cleanup

This plan does **not** cover implementing the OAuth browser terminal itself beyond preserving the boundary that OAuth remains separate from run logging.

---

## Phase 0 - Design alignment and implementation scaffolding

### Goal

Align the codebase, docs, and feature boundaries before changing runtime behavior.

### Tasks

- [x] Confirm the canonical implementation target is `docs/ManagedAgents/LiveLogs.md`.
- [x] Inventory current managed-run logging, transcript, `tmate`, `web_ro`, and terminal-embed code paths.
- [x] Identify all UI surfaces that currently present "Live Output", embedded terminals, or session-viewer semantics.
- [x] Identify current data models and DTOs that store log/session metadata for managed runs.
- [x] Identify current artifact-writing paths for stdout, stderr, transcripts, and diagnostics.
- [x] Decide where the new observability service layer will live in the backend.
- [x] Define feature flags for incremental rollout, including a `logStreamingEnabled` flag.
- [x] Define the migration boundary between legacy session-based observability and the new MoonMind-owned log model.
- [ ] Update any stale docs/specs that still describe `tmate web_ro` as the primary live-log path.
- [x] Create implementation issues/tasks for each phase in this plan.

### Exit criteria

- [x] The team has a single agreed backend architecture for artifact-backed logs and SSE streaming.
- [x] The team has a list of current files/modules that must be changed.
- [x] Legacy terminal assumptions are explicitly marked deprecated for managed-run observability.

---

## Phase 1 - Runtime capture contract and durable artifact production

### Goal

Make managed runs always capture raw `stdout` and `stderr` durably, independent of any UI attachment.

### Tasks

- [x] Update the managed launcher to always start subprocesses with piped `stdout` and `stderr`.
- [x] Remove any requirement that managed runs be wrapped in `tmate` or similar terminal relays for visibility.
- [x] Ensure the supervisor drains `stdout` and `stderr` concurrently and continuously.
- [x] Preserve raw stream fidelity; do not normalize subprocess output into framework logs before persistence.
- [x] Implement or finalize spool/buffer handling for long-running streams.
- [x] Write durable stdout artifacts for every managed run.
- [x] Write durable stderr artifacts for every managed run.
- [x] Write diagnostics artifacts for every managed run.
- [x] Optionally generate a merged log artifact if that materially simplifies retrieval or support workflows.
- [x] Record artifact refs and summary metadata when the run ends.
- [x] Capture and persist exit code, failure class, timestamps, and run summary fields needed by the UI.
- [x] Ensure artifact generation succeeds even when the frontend never connects.
- [x] Ensure supervisor heartbeat and timeout handling integrate cleanly with log capture.
- [x] Add tests for successful runs, failed runs, timed-out runs, and abrupt process termination.
- [x] Add tests for high-volume log output and interleaved stdout/stderr.

### Exit criteria

- [x] Every managed run produces durable stdout, stderr, and diagnostics outputs.
- [x] Log capture no longer depends on interactive terminal infrastructure.
- [x] Raw stdout/stderr fidelity is preserved well enough for replay, download, and troubleshooting.

---

## Phase 2 - Observability data model and backend read APIs

### Goal

Expose artifact-backed observability through MoonMind-owned backend APIs and records.

### Tasks

- [x] Add or update the managed run persistence model to store `stdout_artifact_ref`, `stderr_artifact_ref`, optional `merged_log_artifact_ref`, `diagnostics_ref`, `last_log_offset`, and `last_log_at`.
- [x] Add any missing fields for `exit_code`, `failure_class`, `error_message`, and live-stream capability metadata.
- [x] Design and implement the replacement or successor to terminal-session-style observability records.
- [x] Deprecate use of `TaskRunLiveSession`-style fields for managed-run log viewing.
- [x] Add an observability summary endpoint for task runs.
- [x] Add stdout tail retrieval endpoint(s).
- [x] Add stderr tail retrieval endpoint(s).
- [x] Add merged tail retrieval endpoint(s).
- [x] Add diagnostics retrieval endpoint(s).
- [x] Add full stdout/stderr download endpoint(s).
- [x] Ensure API responses are stable, typed, and suitable for Mission Control consumption.
- [x] Define the API payload shape for log records, including sequence, stream, offset, timestamp, and text.  
- [x] Add authorization checks for observability endpoints.
- [x] Add tests for ended runs, missing artifacts, partial artifacts, and failed diagnostics generation.
- [x] Add tests for tail semantics, pagination/range behavior, and large artifacts.

### Exit criteria

- [x] Mission Control can fetch observability metadata without relying on terminal-session endpoints.
- [x] Stdout, stderr, diagnostics, and merged-tail retrieval all work from MoonMind APIs.
- [x] The persisted model matches the contract described in `LiveLogs.md`.

---

## Phase 3 - Live log stream pipeline

### Goal

Add MoonMind-owned live log delivery for active runs, with artifacts still remaining authoritative.

### Tasks

- [ ] Choose the first transport as SSE over `text/event-stream`.
- [ ] Implement a live log publisher that fans out log chunks to active subscribers.
- [ ] Assign monotonically increasing sequence values for emitted log records.
- [ ] Include `stream`, `offset`, `timestamp`, and raw `text` for each event.
- [ ] Add a `GET /api/task-runs/{id}/logs/stream` endpoint or equivalent.
- [ ] Support filtering by stream where practical.
- [ ] Support resume semantics using `since=<sequence_or_offset>` or equivalent.
- [ ] Ensure reconnect behavior works after transient disconnects.
- [ ] Ensure closed/collapsed clients stop receiving live updates promptly.
- [ ] Ensure stream lifecycle metadata is reflected in the observability summary.
- [ ] Implement graceful fallback to artifact-backed tail retrieval when live streaming is unavailable.
- [ ] Add supervisor/system events to the merged stream only as clearly identified `system` entries.
- [ ] Add tests for reconnection, sequence continuity, run completion, and stream shutdown.
- [ ] Add tests for multiple concurrent viewers observing the same run.
- [ ] Add tests for runs with no live-stream support and for viewers that connect after the run has already ended.

### Exit criteria

- [ ] Operators can open a live stream for an active run and receive appended log records.
- [ ] Reconnect-from-last-sequence behavior works reliably enough for normal browser refreshes and short disconnects.
- [ ] When streaming is unavailable, the UX still works via artifact-backed retrieval.

---

## Phase 4 - Mission Control observability UI

### Goal

Replace terminal-style live output with a MoonMind-native observability surface.

### Tasks

- [ ] Create or update the task detail page Observability section.
- [ ] Implement the **Live Logs** panel using a native React log viewer.
- [ ] Implement the **Stdout** panel backed by stdout retrieval/download APIs.
- [ ] Implement the **Stderr** panel backed by stderr retrieval/download APIs.
- [ ] Implement the **Diagnostics** panel backed by diagnostics APIs.
- [ ] Keep **Artifacts** visible and consistent with the rest of Mission Control.
- [ ] Use `react-virtuoso` or the chosen virtualized rendering base for long/growing log streams.
- [ ] Use `anser` or the chosen ANSI parser for styled rendering of ANSI output.
- [ ] Use TanStack Query for initial tail fetches, cache invalidation, and fallback retrieval.
- [ ] Use `EventSource` for live follow mode.
- [ ] Default the Live Logs panel to collapsed with no active connection.
- [ ] On open, fetch observability summary and merged tail before connecting to live updates.
- [ ] Stop streaming when the panel is collapsed.
- [ ] Stop or pause streaming when the tab is backgrounded; reconnect when visibility returns.
- [ ] Surface viewer states such as `not_available`, `starting`, `live`, `ended`, and `error`.
- [ ] Show per-line stream provenance (`stdout`, `stderr`, `system`).
- [ ] Add wrap toggle, copy support, and download affordances.
- [ ] Ensure ended runs show useful artifact-backed logs without attempting live streaming.
- [ ] Remove terminal embed behavior for managed-run logs.
- [ ] Add UI tests for load states, reconnect behavior, collapse behavior, and ended-run behavior.

### Exit criteria

- [ ] The task detail page no longer depends on an embedded terminal for managed-run logs.
- [ ] Operators can inspect live logs, stdout, stderr, diagnostics, and artifacts in one coherent area.
- [ ] Opening and closing the panel has the expected connection lifecycle behavior.

---

## Phase 5 - Intervention separation and control-surface cleanup

### Goal

Make sure logging remains passive observation while intervention uses explicit workflow/provider controls.

### Tasks

- [ ] Audit the current UI and backend for places where live output and intervention are coupled.
- [ ] Remove assumptions that a live log session implies shell access or operator control access.
- [ ] Define the Intervention panel or controls separately from the log viewer.
- [ ] Ensure Pause/Resume/Cancel/Approve/Reject/Send Message actions route through Temporal signals/updates or provider-native adapters rather than terminal transport.
- [ ] Ensure intervention actions are logged/audited separately from stdout/stderr.
- [ ] Ensure the live log viewer can show future inline system annotations without becoming an intervention mechanism itself.
- [ ] Add tests verifying intervention actions do not require a live log connection.
- [ ] Update UI language to clearly distinguish observation from control.

### Exit criteria

- [ ] Managed-run logs are a passive observability surface only.
- [ ] Intervention is explicit, auditable, and transport-independent.
- [ ] No managed-run operator action depends on a terminal embed or log session attachment.

---

## Phase 6 - Migration off legacy session-based observability

### Goal

Retire legacy `tmate`/terminal-session assumptions for managed-run log viewing without breaking existing runs.

### Tasks

- [ ] Add compatibility handling for historical runs that only have legacy session/transcript data.
- [ ] Decide how old runs should appear in the new Observability UI when stdout/stderr artifacts are missing.
- [ ] Mark legacy terminal-session observability records as deprecated in code and docs.
- [ ] Remove or gate old `web_ro`-driven viewer paths for managed runs.
- [ ] Remove launcher/runtime branches that enabled `tmate` only for live-log visibility.
- [ ] Remove obsolete DTOs, frontend state, and API paths once replacement coverage is complete.
- [ ] Update docs that previously described terminal embedding as the standard managed-run observability path.
- [ ] Ensure OAuth docs still retain `xterm.js` where interactive terminal behavior is actually needed.
- [ ] Add migration notes for operators and contributors.
- [ ] Add regression tests covering both migrated and non-migrated runs.

### Exit criteria

- [ ] Managed-run observability no longer depends on legacy session-viewer infrastructure.
- [ ] Historical runs degrade gracefully.
- [ ] The docs consistently describe the new architecture.

---

## Phase 7 - Hardening, performance, and rollout

### Goal

Make the system production-ready and safe to enable by default.

### Tasks

- [ ] Measure initial-tail response times against the success target.
- [ ] Load test large logs and long-running streams.
- [ ] Validate that the UI remains responsive with very large merged tails.
- [ ] Tune tail sizes, buffering, and reconnect windows.
- [ ] Add observability for the observability system itself, including structured supervisor events and metrics.
- [ ] Correlate log-streaming operations with OpenTelemetry traces/metrics where applicable.
- [ ] Add alerting or health indicators for stream failures, artifact write failures, and diagnostics generation failures.
- [ ] Validate security and authorization on all observability and download endpoints.
- [ ] Validate behavior across browser refreshes, tab visibility changes, and network interruptions.
- [ ] Roll out behind feature flags.
- [ ] Run a staged rollout in local/dev, then broader internal usage, then default-on if successful.
- [ ] Remove feature flag gates only after operational confidence is high.

### Exit criteria

- [ ] The feature meets the core success criteria from `LiveLogs.md`.
- [ ] Operators can reliably observe active and completed runs.
- [ ] The system is stable enough to become the default managed-run log experience.

---

## Cross-phase engineering checklist

### Backend

- [ ] Launcher uses direct subprocess pipe capture for all managed runs.
- [ ] Supervisor owns draining, durability, diagnostics, and live fan-out.
- [ ] Observability APIs are MoonMind-owned and artifact-backed.
- [ ] Live streaming is optional and never authoritative.
- [ ] Legacy terminal/session models are deprecated for managed-run observability.

### Frontend

- [ ] Live Logs is a native log viewer, not a terminal emulator.
- [ ] Stdout, Stderr, Diagnostics, and Artifacts are separate operator surfaces.
- [ ] Connection lifecycle follows panel-open and tab-visibility behavior.
- [ ] Ended runs remain fully inspectable without live transport.

### Architecture boundaries

- [ ] Logging is separated from intervention.
- [ ] OAuth remains the only place where `xterm.js` is required.
- [ ] Durable artifacts remain the source of truth.

---

## Suggested implementation order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 4 baseline with artifact-backed retrieval only
5. Phase 3 live streaming
6. Phase 4 live-follow integration
7. Phase 5
8. Phase 6
9. Phase 7

This order gives Mission Control a usable artifact-backed viewer before SSE live follow is fully complete.

---

## Definition of done

The Live Logs implementation is done when all of the following are true:

- [ ] Every managed run produces durable stdout, stderr, and diagnostics artifacts.
- [ ] Mission Control can display artifact-backed log tails and diagnostics for completed runs.
- [ ] Mission Control can live-follow active runs through MoonMind-owned streaming.
- [ ] Managed-run log viewing no longer depends on `tmate`, `web_ro`, or terminal embedding.
- [ ] Intervention is separate from logging in both UI and backend contracts.
- [ ] `xterm.js` remains limited to OAuth/interactive auth terminal flows.
- [ ] Legacy session-based observability for managed-run logs has been removed or cleanly deprecated.
