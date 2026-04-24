# Feature Specification: live-logs-phase-2

**Feature Branch**: `111-live-logs-phase-2`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 2 from docs/ManagedAgents/LiveLogs.md"

## Source Document Requirements

- **DOC-REQ-015**: Add or update the managed run persistence model to store `stdout_artifact_ref`, `stderr_artifact_ref`, optional `merged_log_artifact_ref`, `diagnostics_ref`, `last_log_offset`, and `last_log_at`.
- **DOC-REQ-016**: Add any missing fields for `exit_code`, `failure_class`, `error_message`, and live-stream capability metadata.
- **DOC-REQ-017**: Design and implement the replacement or successor to terminal-session-style observability records.
- **DOC-REQ-018**: Deprecate use of `TaskRunLiveSession`-style fields for managed-run log viewing.
- **DOC-REQ-019**: Add an observability summary endpoint for task runs.
- **DOC-REQ-020**: Add stdout tail retrieval endpoint(s).
- **DOC-REQ-021**: Add stderr tail retrieval endpoint(s).
- **DOC-REQ-022**: Add merged tail retrieval endpoint(s).
- **DOC-REQ-023**: Add diagnostics retrieval endpoint(s).
- **DOC-REQ-024**: Add full stdout/stderr download endpoint(s).
- **DOC-REQ-025**: Ensure API responses are stable, typed, and suitable for Mission Control consumption.
- **DOC-REQ-026**: Define the API payload shape for log records, including sequence, stream, offset, timestamp, and text.
- **DOC-REQ-027**: Add authorization checks for observability endpoints.
- **DOC-REQ-028**: Add tests for ended runs, missing artifacts, partial artifacts, and failed diagnostics generation.
- **DOC-REQ-029**: Add tests for tail semantics, pagination/range behavior, and large artifacts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Observability API (Priority: P1)

The system provides REST endpoints providing run summaries and stdout/stderr artifact tailing for operators.

**Why this priority**: MoonMind operators need to fetch stdout and stderr via REST without navigating the legacy tmate endpoints.

**Independent Test**: Can be tested via REST client hitting the new proxy API routes for logs and validating the format payload schema matches UI expectations.

**Acceptance Scenarios**:

1. **Given** a completed agent run, **When** `/api/task-runs/{id}/observability-summary` is fetched, **Then** all artifact references and metadata are present.
2. **Given** a completed agent run with `stdout.log`, **When** the `/api/task-runs/{id}/logs/stdout` endpoint is hit, **Then** the raw contents from the file are returned up to the desired tail window.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose REST endpoints under `/api/task-runs/{run_id}/...` for `observability-summary`, `logs/stdout`, `logs/stderr`, `logs/merged`, and `diagnostics`.
- **FR-002**: System MUST separate schemas such that `last_log_offset` and separated artifact refs (`stdout_artifact_ref`, `stderr_artifact_ref`) replaces the single `log_artifact_ref`. 
- **FR-003**: System MUST provide pagination or offset range behaviors to extract partial bytes to tail standard output endpoints.
- **FR-004**: System MUST authorize observability calls against the same scope used for inspecting task-runs.

### Key Entities

- **LogRecord**: Payload shape including sequence, stream (`stdout`/`stderr`), offset, time, and ascii text.
- **ObservabilitySummary**: A payload representing tracking status, artifact refs, capabilities, failure_class, exit_code, and offsets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: REST APIs implemented correctly for summary, stdout, stderr, diagnostic outputs.
- **SC-002**: Legacy TaskRunLiveSession code paths deprecated or disabled for managed runs.
- **SC-003**: 100% unit test coverage for `ObservabilitySummary` and tailing range fetches across partial files and omitted log streams.
