# Feature Specification: Live Logs Session Timeline

**Feature Branch**: `138-live-logs-session-timeline`  
**Created**: 2026-04-07  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 and Phase 1 using test-driven development from the Live Logs Session-Aware Implementation Plan. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Re-baseline the rollout around the shipped live logs stack (Priority: P1)

MoonMind engineers need the implementation tracker and runtime boot payload to reflect that artifact capture, spool transport, SSE follow mode, and merged-tail retrieval already exist so the remaining work is clearly framed as a session-aware observability upgrade.

**Why this priority**: If the rollout plan and feature gating still describe Live Logs as a greenfield build, follow-on implementation work will target the wrong baseline and regress shipped operator behavior.

**Independent Test**: Load the implementation tracker and runtime boot payload and confirm they expose the new session-timeline rollout contract without removing the existing live-log transport baseline.

**Acceptance Scenarios**:

1. **Given** the current repository state already ships artifact-backed stdout, stderr, diagnostics, merged-tail retrieval, spool transport, and SSE follow mode, **When** the implementation tracker is refreshed, **Then** those capabilities are marked as baseline-complete and the remaining work is described as a session-aware upgrade.
2. **Given** Mission Control needs an explicit rollout boundary for the new timeline contract, **When** runtime configuration is built, **Then** the boot payload exposes a dedicated session-timeline feature flag and rollout scope instead of overloading the older log-streaming toggle.

---

### User Story 2 - Persist one canonical observability timeline contract (Priority: P1)

Backend and frontend engineers need one MoonMind-defined observability event shape that represents stdout, stderr, system annotations, and session-plane lifecycle rows so historical and live views can use the same contract.

**Why this priority**: The session-aware timeline cannot be implemented safely if browser and backend surfaces still depend on chunk-specific or provider-specific payload assumptions.

**Independent Test**: Produce and persist mixed stdout, stderr, system, and session events for one managed run, then confirm the durable history can be reconstructed from a single normalized event contract.

**Acceptance Scenarios**:

1. **Given** a managed run emits output bytes and session-plane lifecycle events, **When** MoonMind records observability history, **Then** every row is represented in one run-global sequence using a single normalized event shape that includes stream, kind, text, timestamp, offset, and optional session snapshot metadata.
2. **Given** the run has already ended, **When** historical observability is loaded later, **Then** MoonMind reconstructs the timeline from durable structured history instead of requiring a live stream or provider-native payload replay.
3. **Given** existing consumers still expect the current live-log chunk shape during migration, **When** the new event contract is introduced, **Then** current live-log retrieval remains readable while the normalized timeline contract becomes the canonical representation.

---

### User Story 3 - Preserve session context in observability summaries and records (Priority: P2)

Operators and future timeline work need the latest session identity snapshot attached to managed-run observability records so resets and thread changes are available without re-querying transient container state.

**Why this priority**: The timeline contract is incomplete unless the durable run record can identify which session, epoch, container, thread, and active turn the operator is looking at.

**Independent Test**: Persist a managed-run observability record with session metadata and verify summary/history readers surface the latest session snapshot and durable event history reference.

**Acceptance Scenarios**:

1. **Given** a managed session is active for a run, **When** the run record is stored or updated, **Then** the latest bounded session identity is stored with the observability record.
2. **Given** a structured observability history file exists for a run, **When** the summary or history reader loads that run later, **Then** the reader can surface the latest session snapshot and durable observability-history reference without inspecting container-local runtime state.

### Edge Cases

- A historical run has stdout/stderr artifacts but no structured observability history yet; the system must continue to degrade gracefully through existing artifact-backed retrieval.
- A run emits only system or session events during a short interval; sequence ordering must still remain run-global and deterministic.
- A session reset occurs while the run is still active; the durable session snapshot and structured event history must remain consistent about the new epoch and thread boundary.
- Structured event persistence fails while artifact capture succeeds; runtime control and durable stdout/stderr/diagnostics capture must remain intact and the failure must not fabricate successful event persistence.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The implementation tracker for Live Logs MUST treat the shipped artifact-first spool/SSE stack as the baseline and MUST describe the remaining work as a session-aware observability upgrade rather than a greenfield build.
- **FR-002**: MoonMind MUST expose a dedicated feature flag for the session-aware timeline contract, including explicit rollout scopes for disabled, internal-only, Codex-managed, and all managed-session-capable runs.
- **FR-003**: MoonMind MUST define one canonical observability event model that can represent stdout, stderr, system annotations, and session-plane lifecycle rows in a shared run-global sequence.
- **FR-004**: The canonical observability event model MUST include sequence, timestamp, stream, kind, text, offset, and optional session identity metadata needed to describe the current session context.
- **FR-005**: MoonMind MUST persist structured observability history durably enough to reconstruct the timeline after the run ends without depending on live transport or provider-native event replay.
- **FR-006**: Managed-run observability records MUST store the latest durable session snapshot and a durable reference to the structured observability history when available.
- **FR-007**: Existing live-log consumers MUST remain readable during migration while the canonical observability event model becomes the source-of-truth contract for timeline history and live streaming.
- **FR-008**: The Phase 0 and Phase 1 slice MUST include production runtime code changes and automated validation tests that prove the new flag wiring, event persistence, and compatibility behavior.

### Key Entities *(include if feature involves data)*

- **Run Observability Event**: One normalized timeline row representing output, supervision, or session lifecycle activity in a shared run-global sequence.
- **Session Snapshot**: The latest bounded session identity attached to a managed run, including session, epoch, container, thread, and active turn context when present.
- **Observability History Reference**: The durable pointer to the structured event history used to reconstruct the timeline after live streaming ends.
- **Session Timeline Rollout Flag**: The explicit runtime toggle that determines whether the session-aware timeline contract is hidden, internal-only, Codex-managed-only, or broadly enabled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The implementation tracker is rewritten so previously shipped artifact/spool/SSE capabilities are marked baseline-complete and the remaining work is clearly scoped to the session-aware upgrade.
- **SC-002**: Automated tests verify the runtime boot payload exposes the new session-timeline flag and preserves the current live-log transport metadata.
- **SC-003**: Automated tests verify one persisted observability history can reconstruct mixed stdout, stderr, system, and session rows in shared sequence order for a completed run.
- **SC-004**: Automated tests verify managed-run observability records preserve the latest session snapshot and structured event-history reference without breaking current live-log readers.
