# Implementation Plan: Cursor CLI Phase 2 — Adapter Wiring

## Technical Context

Phase 2 wires the Cursor CLI (installed in Phase 1) into MoonMind's managed agent runtime by modifying the adapter layer, launcher command builder, and adding a new NDJSON output parser. All changes are in Python; no infrastructure (Docker/Compose) changes needed.

## Constitution Check

- ✅ No credentials committed (CURSOR_API_KEY handled via env vars)
- ✅ No compatibility transforms (cursor_cli is a new runtime, no fallback behavior)
- ✅ Fail-fast for unsupported values

## Project Structure

Changes touch 3 existing files and create 1 new module:

```
moonmind/agents/base/adapter.py           # MODIFY: add cursor_cli branch + CURSOR_API_KEY scrubbing
moonmind/workflows/temporal/runtime/launcher.py  # MODIFY: add cursor_cli build_command logic
moonmind/agents/base/ndjson_parser.py      # NEW: NDJSON stream parser for Cursor's stream-json output
tests/unit/agents/base/test_adapter.py     # MODIFY: add cursor_cli tests
tests/unit/agents/base/test_ndjson_parser.py  # NEW: unit tests for NDJSON parser
tests/unit/services/temporal/runtime/test_launcher.py  # MODIFY: add cursor_cli command tests
```

No changes to `activity_catalog.py` needed — cursor_cli dispatches through the existing `agent_runtime` fleet via `ManagedRuntimeProfile.runtime_id`. No new activity types or fleet definitions required.

---

## Implementation Details

### 1. Adapter: Volume Mount Resolution (DOC-REQ-P2-001)

Add `cursor_cli` branch to `resolve_volume_mount_env()` in `adapter.py`:
```python
elif runtime_id == "cursor_cli":
    shaped_env["CURSOR_CONFIG_DIR"] = volume_mount_path
```

### 2. Adapter: OAuth Scrubbing (DOC-REQ-P2-002)

Add `CURSOR_API_KEY` to the `oauth_scrubbable_keys` list in `shape_agent_environment()`.

### 3. Launcher: Command Construction (DOC-REQ-P2-003)

Extend `build_command()` in `launcher.py` to handle `cursor_cli` runtime:
- Start with `cursor-agent` binary (from `profile.command_template`)
- Add `-p` flag with instruction text from `request.instruction_ref`
- Add `--output-format stream-json` and `--force`
- Optionally add `--model` and `--sandbox` from request parameters

The existing `build_command()` dispatches on `profile.runtime_id` for gemini_cli-specific `--yolo --prompt` flags. We add a similar branch for `cursor_cli`.

### 4. NDJSON Parser Module (DOC-REQ-P2-004)

Create `moonmind/agents/base/ndjson_parser.py` with:
- `CursorStreamEvent` dataclass with `event_type`, `timestamp`, `data` fields
- `parse_ndjson_line(line: str) -> CursorStreamEvent | None` function
- `parse_ndjson_stream(lines: Iterable[str]) -> Iterator[CursorStreamEvent]` generator
- Graceful handling of malformed lines (log warning, skip)

### 5. Worker Fleet Registration (DOC-REQ-P2-005)

No `activity_catalog.py` changes needed. Cursor CLI runs through the existing `agent_runtime` fleet via `ManagedRuntimeProfile` with `runtime_id="cursor_cli"`. The `agent_runtime.launch` activity already handles arbitrary runtime profiles. However, we should document this in research.md for clarity.

---

## Verification Plan

### Automated Tests

All tests run via `./tools/test_unit.sh` (single source of truth per AGENTS.md).

#### Existing Tests (verify no regression)
```bash
./tools/test_unit.sh
```

#### New Tests

1. **`tests/unit/agents/base/test_adapter.py`** — add 2 tests:
   - `test_resolve_volume_mount_env_cursor()`: verify `cursor_cli` → `CURSOR_CONFIG_DIR`
   - `test_shape_agent_environment_oauth_includes_cursor_key()`: verify `CURSOR_API_KEY` is scrubbed

2. **`tests/unit/agents/base/test_ndjson_parser.py`** — new file with 4 tests:
   - `test_parse_ndjson_line_valid()`: parse each event type
   - `test_parse_ndjson_line_malformed()`: returns None for bad JSON
   - `test_parse_ndjson_stream()`: yields events from multi-line input
   - `test_parse_ndjson_stream_skips_malformed()`: skips bad lines, yields good ones

3. **`tests/unit/services/temporal/runtime/test_launcher.py`** — add 2 tests:
   - `test_build_command_cursor_cli()`: verify cursor-specific flags
   - `test_build_command_cursor_cli_with_sandbox()`: verify --sandbox flag

### Manual Verification

None required — all changes are unit-testable Python code.
