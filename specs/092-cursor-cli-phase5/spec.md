# Spec: Cursor CLI Phase 5 — Testing and Hardening

**Source Document**: [CursorCli.md](file:///Users/nsticco/MoonMind/docs/ManagedAgents/CursorCli.md)
**Phase**: 5 of 5

---

## Document Requirement Identifiers

| ID | Source Section | Requirement |
|----|---------------|-------------|
| DOC-REQ-P5-001 | §13 Phase 5 | Unit tests for command construction and output parsing |
| DOC-REQ-P5-002 | §13 Phase 5 | Integration test: end-to-end headless execution in Docker |
| DOC-REQ-P5-003 | §13 Phase 5 | Verify 429/rate-limit detection and cooldown signaling |
| DOC-REQ-P5-004 | §13 Phase 5 | Verify cancellation (SIGTERM → SIGKILL) path |
| DOC-REQ-P5-005 | §13 Phase 5 | Dashboard visibility for `cursor_cli` runs |

---

## User Stories

### US1: Rate-Limit Detection
**As a** MoonMind agent runtime  
**I want** NDJSON stream events to be scanned for 429/rate-limit indicators  
**So that** the `AuthProfileManager` can be signaled to apply cooldowns automatically

### US2: Graceful Cancellation
**As a** MoonMind operator cancelling a cursor_cli run  
**I want** the process to receive SIGTERM first, then SIGKILL after a grace period  
**So that** the agent has a chance to clean up gracefully

### US3: Dashboard Visibility
**As a** MoonMind dashboard user  
**I want** `cursor_cli` runs to display with a clean "Cursor CLI" label  
**So that** runtime identification is consistent across the dashboard

---

## Functional Requirements

### Rate-Limit Detection

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-001 | `detect_rate_limit()` function scans a `CursorStreamEvent` for rate-limit indicators (429 status, "rate limit" text in error data) | DOC-REQ-P5-003 | Unit test |
| FR-002 | `detect_rate_limit()` returns a dict with `detected: bool` and `retry_after_seconds: int` | DOC-REQ-P5-003 | Unit test |
| FR-003 | Rate-limit detection handles gracefully when no indicators are present | DOC-REQ-P5-003 | Unit test |

### Process Cancellation

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-004 | `cancel_managed_process()` sends SIGTERM, waits a grace period, then sends SIGKILL if not exited | DOC-REQ-P5-004 | Unit test |
| FR-005 | Grace period defaults to 5 seconds and is configurable | DOC-REQ-P5-004 | Unit test |
| FR-006 | If process exits during grace period, SIGKILL is not sent | DOC-REQ-P5-004 | Unit test |

### Dashboard Visibility

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-007 | `TASK_RUNTIME_LABELS` in `dashboard.js` includes `cursor_cli: "Cursor CLI"` | DOC-REQ-P5-005 | Unit test (view model) |

### Existing Coverage (Phase 2)

| ID | Requirement | DOC-REQ | Status |
|----|-------------|---------|--------|
| FR-008 | Command construction tests for `cursor_cli` | DOC-REQ-P5-001 | ✅ Done (Phase 2, 3 tests) |
| FR-009 | NDJSON output parsing tests | DOC-REQ-P5-001 | ✅ Done (Phase 2, 12 tests) |

---

## Success Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| SC-1 | `detect_rate_limit()` correctly identifies 429 events | Unit test |
| SC-2 | `cancel_managed_process()` follows SIGTERM→grace→SIGKILL pattern | Unit test |
| SC-3 | Dashboard shows "Cursor CLI" label for cursor_cli runtime | Unit test |
| SC-4 | All existing + new unit tests pass (`./tools/test_unit.sh`) | CLI verification |
