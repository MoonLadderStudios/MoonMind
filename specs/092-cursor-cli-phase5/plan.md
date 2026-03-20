# Implementation Plan: Cursor CLI Phase 5 — Testing and Hardening

## Technical Context

Phase 5 adds hardening utilities (rate-limit detection, graceful cancellation) and dashboard visibility for `cursor_cli` runs. The command construction and output parsing tests were already done in Phase 2 (17 tests). Docker integration testing is deferred as it requires the full stack.

## Constitution Check

- ✅ No credentials committed
- ✅ No compatibility transforms
- ✅ Fail-fast for unsupported values

## Proposed Changes

### Rate-Limit Detection

#### [MODIFY] [ndjson_parser.py](file:///Users/nsticco/MoonMind/moonmind/agents/base/ndjson_parser.py)

Add `detect_rate_limit(event: CursorStreamEvent) -> dict[str, Any]` function:
- Checks `event.data` for HTTP status codes (429)
- Checks for "rate limit" or "rate_limit" text in error messages
- Returns `{"detected": bool, "retry_after_seconds": int | None}`
- `retry_after_seconds` extracted from `Retry-After` header if present in data

### Process Cancellation

#### [NEW] [process_control.py](file:///Users/nsticco/MoonMind/moonmind/workflows/temporal/runtime/process_control.py)

Standalone module for managed process lifecycle control:
- `cancel_managed_process(process, grace_seconds=5.0) -> int | None` — SIGTERM → wait → SIGKILL
- Returns the exit code if process exited, None if still running after SIGKILL

### Dashboard Label

#### [MODIFY] [dashboard.js](file:///Users/nsticco/MoonMind/api_service/static/task_dashboard/dashboard.js)

Add `cursor_cli: "Cursor CLI"` entry to `TASK_RUNTIME_LABELS` dict (line ~307).

### Tests

#### [MODIFY] [test_ndjson_parser.py](file:///Users/nsticco/MoonMind/tests/unit/agents/base/test_ndjson_parser.py)

Add tests for `detect_rate_limit()`:
- Event with 429 status code
- Event with "rate limit" error text
- Event with no rate-limit indicators
- Event with `retry_after_seconds` value

#### [NEW] [test_process_control.py](file:///Users/nsticco/MoonMind/tests/unit/services/temporal/runtime/test_process_control.py)

Tests for `cancel_managed_process()`:
- Process exits during grace period (no SIGKILL)
- Process doesn't exit, SIGKILL sent
- Already terminated process

#### [MODIFY] [test_task_dashboard_view_model.py](file:///Users/nsticco/MoonMind/tests/unit/api_service/views/test_task_dashboard_view_model.py)

Add assertion that `TASK_RUNTIME_LABELS` contains `cursor_cli` (if testable from Python side).

---

## Verification Plan

### Automated Tests

```bash
./tools/test_unit.sh
```

New tests:
1. Rate-limit detection: 4+ tests in `test_ndjson_parser.py`
2. Process cancellation: 3+ tests in `test_process_control.py`
3. All existing 1768 tests continue to pass

### Manual Verification

Docker integration test deferred — requires full managed agent stack with Cursor CLI binary.
