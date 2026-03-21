# Research Notes: Cursor CLI Phase 5

## R1: Existing Test Coverage (Phase 2)

Phase 2 already added 17 tests covering DOC-REQ-P5-001:
- `test_ndjson_parser.py`: 12 tests — stream parsing, event types, malformed lines, empty lines
- `test_launcher.py`: 3 tests — `cursor_cli` command construction, sandbox flag
- `test_adapter.py`: 2 tests — volume mount env, API key scrubbing

## R2: Rate-Limit Detection Patterns

Cursor CLI's NDJSON stream may emit error events when rate-limited. Indicators:
- `event.data.status == 429` or `event.data.statusCode == 429`
- `event.data.error` or `event.data.message` containing "rate limit"
- `event.data.retry_after` or `event.data.retryAfter` header value

The `detect_rate_limit()` function should check all of these patterns.

## R3: Process Cancellation Patterns

The launcher currently spawns `asyncio.subprocess.Process` objects but has no cancellation helper. The standard UNIX pattern is:
1. Send SIGTERM
2. Wait grace period (5s default)
3. Send SIGKILL if not exited

Python's `asyncio.subprocess.Process` exposes `.send_signal()`, `.terminate()`, and `.kill()`.

## R4: Dashboard Runtime Labels

`TASK_RUNTIME_LABELS` at `dashboard.js` L307-312 maps:
- `codex` → "Codex CLI"
- `gemini` → "Gemini CLI"
- `claude` → "Claude Code"
- `jules` → "Jules"

Note: `gemini` key (not `gemini_cli`) — the `formatRuntimeLabel()` function at L373 does loose matching. We should use `cursor_cli` as the key to match the runtime_id.

## R5: Docker Integration Test (Deferred)

End-to-end Docker test requires:
- cursor-agent binary installed in container
- Valid CURSOR_API_KEY
- Full Temporal stack running
- Not feasible in unit test CI — deferred to manual testing
