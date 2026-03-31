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
- The live stream transport must be cross-process; an API-local in-memory publisher is not the architecture boundary.

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

## Known current state (as of 2026-03-30)

This section captures an honest snapshot of the implementation, to prevent future confusion about what is and is not done:

- **Done**: Durable stdout/stderr/diagnostics artifact production is substantially implemented in the managed runtime supervisor.
- **Done**: Data model fields (`stdout_artifact_ref`, `stderr_artifact_ref`, `diagnostics_ref`, `last_log_at`, `last_log_offset`) exist and are populated.
- **Partial**: Observability read APIs (summary, tail endpoints) exist but are not yet fully consumed by Mission Control.
- **Partial / not yet used**: Merged-tail semantics exist in outline but the contract has not been hardened; Mission Control does not yet call these APIs.
- **Not started**: The runtime supervisor does not yet emit live log records into any shared transport consumed by the API or UI.
- **Not started**: The full Mission Control observability panel described in `LiveLogs.md` is not yet implemented; the task detail page currently uses a thin SSE tail view.
- **Gap**: The current SSE endpoint, if it exists, is not yet fed by actual supervised runtime output chunks.

---

## Phase 0 — Design alignment and implementation scaffolding

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
- [x] Update any stale docs/specs that still describe `tmate web_ro` as the primary live-log path.
- [x] Create implementation issues/tasks for each phase in this plan.

### Exit criteria

- [x] The team has a single agreed backend architecture for artifact-backed logs and SSE streaming.
- [x] The team has a list of current files/modules that must be changed.
- [x] Legacy terminal assumptions are explicitly marked deprecated for managed-run observability.

---

## Phase 1 — Runtime capture contract and durable artifact production

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

## Phase 2 — Observability data model and backend read APIs

### Goal

Expose artifact-backed observability through MoonMind-owned backend APIs and records.

### Completion status of tasks (honest labels)

- [x] **complete** — Add or update the managed run persistence model to store `stdout_artifact_ref`, `stderr_artifact_ref`, optional `merged_log_artifact_ref`, `diagnostics_ref`, `last_log_offset`, and `last_log_at`.
- [x] **complete** — Add any missing fields for `exit_code`, `failure_class`, `error_message`, and live-stream capability metadata.
- [x] **complete** — Design and implement the replacement or successor to terminal-session-style observability records.
- [x] **complete** — Deprecate use of `TaskRunLiveSession`-style fields for managed-run log viewing.
- [x] **complete** — Add an observability summary endpoint for task runs.
- [x] **complete** — Add stdout tail retrieval endpoint(s).
- [x] **complete** — Add stderr tail retrieval endpoint(s).
- [x] **complete** — Add merged tail retrieval endpoint(s). Endpoint exists and merged-tail contract semantics (ordering guarantees, fallback from stdout/stderr when no merged artifact exists) have been hardened.
- [x] **complete** — Add diagnostics retrieval endpoint(s).
- [x] **complete** — Add full stdout/stderr download endpoint(s).
- [x] **complete** — Ensure API responses are stable, typed, and suitable for Mission Control consumption.
- [x] **complete** — Define the API payload shape for log records, including sequence, stream, offset, timestamp, and text.
- [x] **complete** — Add authorization checks for observability endpoints.
- [x] **complete** — Add tests for ended runs, missing artifacts, partial artifacts, and failed diagnostics generation.
- [x] **complete** — Add tests for tail semantics, pagination/range behavior, and large artifacts.
- [x] **complete** — Mission Control calls the summary and tail APIs from the main task detail page.

### Exit criteria

- [x] **complete** — Mission Control can fetch observability metadata without relying on terminal-session endpoints.
- [x] **complete** — Stdout, stderr, diagnostics, and merged-tail retrieval all work from MoonMind APIs.
- [x] **complete** — The persisted model matches the contract described in `LiveLogs.md`.
- [x] **complete** — Mission Control actually consumes the observability APIs on task detail page load. This is required before Phase 2 exit criteria are fully met.

---

## Phase 3 — Live log stream pipeline

### Goal

Add MoonMind-owned live log delivery for active runs, with artifacts still remaining authoritative.

### Pre-step: Choose the shared live-stream transport

Before implementing the publisher, an explicit design decision must be made and documented:

- [x] Choose the real cross-process streaming mechanism.
  - Options: Redis pub/sub, shared append-only spool file, DB-backed tailing (e.g. polling a log records table), or another MoonMind-owned mechanism.
  - **Reject**: API-local singleton memory is not a valid architecture boundary (see `LiveLogs.md` §6.4).
- [x] Document the chosen mechanism in a short ADR or inline note in this plan.
- [x] Confirm the chosen mechanism works when the managed runtime supervisor runs in a different process or container from the API service.

### Tasks

- [x] Choose the first transport as SSE over `text/event-stream` (client-side delivery — already decided).
- [x] Implement a live log publisher that fans out log chunks to active subscribers via the chosen cross-process transport.
- [x] Assign monotonically increasing sequence values for emitted log records.
- [x] Include `stream`, `offset`, `timestamp`, and raw `text` for each event.
- [x] Wire the runtime supervisor to actually emit live log records into the shared transport (this is the key missing producer-to-stream link).
- [x] Add a `GET /api/task-runs/{id}/logs/stream` endpoint or equivalent that consumes from the shared transport (not from an API-local buffer).
- [x] Support filtering by stream where practical.
- [x] Support resume semantics using `since=<sequence_or_offset>` or equivalent; resume is best-effort.
- [x] Ensure reconnect behavior works after transient disconnects; artifacts are the durable fallback when the resume window has expired.
- [x] Ensure closed/collapsed clients stop receiving live updates promptly.
- [x] Ensure stream lifecycle metadata (`live_stream_status`, `supports_live_streaming`) is reflected in the observability summary.
- [x] Implement graceful fallback to artifact-backed tail retrieval when live streaming is unavailable.
- [x] Add supervisor/system events to the merged stream only as clearly identified `system` entries.
- [x] Add tests for reconnection, sequence continuity, run completion, and stream shutdown.
- [x] Add tests for multiple concurrent viewers observing the same run.
- [x] Add tests for runs with no live-stream support and for viewers that connect after the run has already ended.

### Exit criteria

- [x] Supervised runtime chunks are actually published into the shared transport consumed by the API endpoint.
- [x] Operators can open a live stream for an active run and receive appended log records that came from actual process output.
- [x] Reconnect-from-last-sequence behavior works reliably enough for normal browser refreshes and short disconnects.
- [x] When streaming is unavailable, the UX still works via artifact-backed retrieval.
- [x] Endpoint presence alone does not satisfy exit criteria; real emitted events from managed runs are required.

---

## Phase 4 — Mission Control observability UI

### Goal

Replace terminal-style live output with a MoonMind-native observability surface.

### Dependency chain

Before this phase can be considered complete, all of the following must exist:

1. backend live event production (Phase 3)
2. backend stream delivery to API via cross-process transport (Phase 3)
3. observability summary and tail fallback (Phase 2 fully consumed by UI)
4. Mission Control panel behavior and state model (this phase)
5. ended-run behavior (this phase)
6. collapse/background lifecycle (this phase)
7. artifact-only degraded mode (this phase)

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
- [ ] On open, fetch observability summary and merged tail before connecting to live updates. Initial content must not depend on SSE success.
- [ ] Stop streaming when the panel is collapsed.
- [ ] Stop or pause streaming when the tab is backgrounded; reconnect when visibility returns only if run is still active.
- [ ] Surface viewer states: `not_available`, `starting`, `live`, `ended`, `error` (defined in `LiveLogs.md` §12.3).
- [ ] Show per-line stream provenance (`stdout`, `stderr`, `system`).
- [ ] Add wrap toggle, copy support, and download affordances.
- [ ] Ensure ended runs show useful artifact-backed logs without attempting live streaming.
- [ ] Remove any thin UI assumptions that SSE alone is sufficient (e.g. current thin SSE tail view on task detail).
- [ ] Remove any remaining implicit dependence on legacy session semantics for managed-run logs.
- [ ] Add UI tests for load states, reconnect behavior, collapse behavior, and ended-run behavior.

### Exit criteria

- [ ] The task detail page no longer depends on an embedded terminal for managed-run logs.
- [ ] Operators can inspect live logs, stdout, stderr, diagnostics, and artifacts in one coherent area.
- [ ] Opening and closing the panel has the expected connection lifecycle behavior.
- [ ] Artifact-backed initial load works independently of whether live streaming succeeds.
- [ ] Completed runs remain fully inspectable without ever having had a live stream connection.

---

## Phase 5 — Intervention separation and control-surface cleanup

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

## Phase 6 — Migration off legacy session-based observability

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

## Phase 7 — Hardening, performance, and rollout

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
- [ ] Supervisor owns draining, durability, diagnostics, and live fan-out via shared cross-process transport.
- [ ] Observability APIs are MoonMind-owned and artifact-backed.
- [ ] Live streaming is optional and never authoritative.
- [ ] Legacy terminal/session models are deprecated for managed-run observability.

### Frontend

- [ ] Live Logs is a native log viewer, not a terminal emulator.
- [ ] Stdout, Stderr, Diagnostics, and Artifacts are separate operator surfaces.
- [ ] Connection lifecycle follows panel-open and tab-visibility behavior.
- [ ] Ended runs remain fully inspectable without live transport.
- [ ] Thin SSE-only assumptions in current task detail UI are removed or replaced.

### Architecture boundaries

- [ ] Logging is separated from intervention.
- [ ] OAuth remains the only place where `xterm.js` is required.
- [ ] Durable artifacts remain the source of truth.
- [ ] Live stream transport is cross-process; API-local singleton memory is not used as the architecture boundary.

---

## Suggested implementation order

1. Phase 0 — doc alignment (**this pass**)
2. Phase 2 fully consumed — artifact-first UI baseline (Mission Control calls observability APIs)
3. Phase 3 pre-step — choose and document cross-process transport
4. Phase 3 — real producer-to-stream plumbing (supervisor emits; API consumes from shared transport)
5. Phase 4 — live-follow integration in Mission Control
6. Phase 5 + Phase 6 — cleanup of legacy assumptions and session migration
7. Phase 7 — hardening and rollout

This order gives Mission Control a usable artifact-backed viewer before SSE live follow is fully complete.

---

## Definition of done

The Live Logs implementation is done when all of the following are true:

- [ ] Every managed run produces durable stdout, stderr, and diagnostics artifacts.
- [ ] Mission Control can display artifact-backed log tails and diagnostics for completed runs.
- [ ] Mission Control can live-follow active runs through MoonMind-owned streaming fed by actual supervised runtime output.
- [ ] Managed-run log viewing no longer depends on `tmate`, `web_ro`, or terminal embedding.
- [ ] Intervention is separate from logging in both UI and backend contracts.
- [ ] `xterm.js` remains limited to OAuth/interactive auth terminal flows.
- [ ] Legacy session-based observability for managed-run logs has been removed or cleanly deprecated.
- [ ] The live stream transport works across the supervisor-to-API process boundary.
