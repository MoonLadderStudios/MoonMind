# Feature Specification: live-logs-phase-1

**Feature Branch**: `110-live-logs-phase-1`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 1 from docs/tmp/009-LiveLogsPlan.md"

## Source Document Requirements

- **DOC-REQ-001**: Update the managed launcher to always start subprocesses with piped `stdout` and `stderr`. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-002**: Remove any requirement that managed runs be wrapped in `tmate` or similar terminal relays for visibility. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-003**: Ensure the supervisor drains `stdout` and `stderr` concurrently and continuously. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-004**: Preserve raw stream fidelity; do not normalize subprocess output into framework logs before persistence. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-005**: Implement or finalize spool/buffer handling for long-running streams (64KB chunks). Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-006**: Write durable `stdout` artifacts for every managed run. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-007**: Write durable `stderr` artifacts for every managed run. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-008**: Write `diagnostics.json` artifacts for every managed run. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-009**: Record artifact refs and summary metadata when the run ends. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-010**: Capture and persist exit code, failure class, timestamps, and run summary fields needed by the UI. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-011**: Ensure artifact generation succeeds even when the frontend never connects. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-012**: Ensure supervisor heartbeat and timeout handling integrate cleanly with log capture. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-013**: Add tests for successful runs, failed runs, timed-out runs, and abrupt process termination. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`
- **DOC-REQ-014**: Add tests for high-volume log output and interleaved stdout/stderr. Source: `docs/tmp/009-LiveLogsPlan.md#Phase 1`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - System Capture Logs (Priority: P1)

The system automatically and transparently captures raw stdout and stderr for all agent runtime executions independently of any active frontend observer.

**Why this priority**: Without reliable raw data capture concurrently, pipeline observability is incomplete and runs can encounter deadlocking.

**Independent Test**: Can be tested via unit tests creating a subprocess with piped streams and ensuring artifacts are generated after duration.

**Acceptance Scenarios**:

1. **Given** an launched agent run, **When** it emits high-volume stdio data, **Then** the log streamer buffers it continuously and writes durable `.log` and `.json` artifacts at the end of the run without exhausting pipe buffers causing stalls.

### User Story 2 - Runtime metadata remains operator-visible after completion (Priority: P1)

Managed runs must persist the artifact references and summary metadata that Mission Control needs after the process exits, times out, or fails.

**Why this priority**: Live log capture is incomplete if the final run record does not retain the stdout/stderr/diagnostics refs and terminal summary fields the UI reads later.

**Independent Test**: Can be tested by supervising successful, failed, and timed-out processes and asserting the resulting `ManagedRunRecord` and diagnostics artifact contain the expected refs and status metadata.

**Acceptance Scenarios**:

1. **Given** a managed run finishes, **When** supervision finalizes the run, **Then** the `ManagedRunRecord` stores `stdout.log`, `stderr.log`, and `diagnostics.json` refs together with summary metadata and timestamps.
2. **Given** a managed run fails or times out, **When** supervision classifies the terminal state, **Then** the diagnostics artifact and run record preserve exit code, failure class, summary, and timestamps needed by the UI.

### User Story 3 - Heartbeat and timeout handling do not block capture (Priority: P1)

Heartbeat updates and timeout enforcement must run alongside stream draining so large-output processes do not deadlock and terminal cleanup still completes deterministically.

**Why this priority**: Concurrency between heartbeating and stream draining is the mechanism that prevents pipe-buffer stalls and preserves unattended execution.

**Independent Test**: Can be tested with long-running and high-volume subprocesses that would block if log streaming started only after process wait completion.

**Acceptance Scenarios**:

1. **Given** a process emits more than a pipe buffer of interleaved stdout/stderr, **When** the supervisor runs, **Then** heartbeat/timeout handling and stream draining proceed concurrently and the process completes without pipe stalls.
2. **Given** a process exceeds its timeout, **When** the supervisor terminates it, **Then** the final diagnostics and persisted run metadata still reflect the timeout outcome and captured output up to termination.

### Edge Cases

- A process exits abruptly before emitting a trailing newline.
- A process emits large interleaved stdout and stderr chunks that exceed the default OS pipe buffer.
- The frontend never connects to a live-log stream while the run is executing.
- Timeout-driven termination occurs after partial output has already been captured.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST write all standard output for a managed run to an artifact named `stdout.log`. (Maps to DOC-REQ-006, DOC-REQ-001)
- **FR-002**: System MUST write all standard error output for a managed run to an artifact named `stderr.log`. (Maps to DOC-REQ-007, DOC-REQ-001)
- **FR-003**: System MUST construct and save a `diagnostics.json` artifact at completion. (Maps to DOC-REQ-008)
- **FR-004**: System MUST record references to these durables within the `ManagedRunRecord`. (Maps to DOC-REQ-009, DOC-REQ-010)
- **FR-005**: System MUST run the log extraction in a concurrent task alongside heartbeating to prevent deadlocking. (Maps to DOC-REQ-003, DOC-REQ-012)
- **FR-006**: System MUST not wrap command invocations inside `tmate`. (Maps to DOC-REQ-002)

### Non-Functional Requirements

- **NFR-001**: Log capture MUST preserve raw subprocess output ordering within each stream and must not rewrite it into framework log formats before artifact persistence. (Maps to DOC-REQ-004)
- **NFR-002**: The runtime path MUST tolerate high-volume output beyond the nominal OS pipe buffer without deadlocking supervised processes. (Maps to DOC-REQ-005, DOC-REQ-014)
- **NFR-003**: The feature MUST remain observer-independent: artifact generation succeeds even when no live-log UI client connects. (Maps to DOC-REQ-011)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of managed runs produce `stdout.log`, `stderr.log`, and `diagnostics.json` artifacts stored via the artifact API layer.
- **SC-002**: Processes emitting large interleaved logs (>1MB) complete successfully within the allocated timeframe without pipe stalls.
- **SC-003**: No managed configurations fallback to or execute legacy `tmate` terminals for standard stdout fetching.
