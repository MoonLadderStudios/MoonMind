# Research Notes: Cursor CLI Phase 2

## R1: No `activity_catalog.py` Changes Needed

The CursorCli.md document lists "Register `cursor_cli` in `activity_catalog.py` and worker capability sets" as a Phase 2 item. However, after analyzing the codebase:

- `activity_catalog.py` defines fleet-level activities (`agent_runtime.launch`, `agent_runtime.cancel`, etc.) and worker fleet capabilities
- Runtime-specific dispatch happens via `ManagedRuntimeProfile.runtime_id`, not via activity types
- The existing `agent_runtime` fleet already handles arbitrary managed runtimes (codex_cli, gemini_cli, claude_code)
- No new activity type or fleet is needed for cursor_cli

**Decision**: Document this discovery but skip activity_catalog changes. Cursor CLI is registered through the profile system, not the activity catalog.

## R2: Launcher `build_command()` Design

The existing `build_command()` method has a `runtime_id`-specific branch for `gemini_cli` (uses `--yolo --prompt` instead of `--instruction-ref`). For cursor_cli:

- Binary name is `cursor-agent` (set via `command_template` in profile)
- Uses `-p` for print/headless mode (equivalent to Codex's `--instruction-ref` or Gemini's `--prompt`)
- Requires `--output-format stream-json` for structured output
- Requires `--force` for auto-applying changes
- Optional `--sandbox` flag from request parameters

**Decision**: Add `cursor_cli` branch in `build_command()` similar to the existing `gemini_cli` branch.

## R3: NDJSON Parser Scope

The parser is intentionally simple for Phase 2:
- Parse individual JSON lines into typed events
- Generator-based for streaming compatibility
- No state machine or event correlation (that's Phase 4/5 territory)
- Malformed lines are skipped with a warning log

**Decision**: Minimal parser that produces structured events. The `ManagedRunSupervisor` will consume these events in a later phase.

## R4: `CURSOR_CONFIG_DIR` vs `CURSOR_HOME`

The CursorCli.md document references `CURSOR_CONFIG_DIR` for the volume mount env var. This aligns with Cursor CLI's configuration directory pattern where it looks for credentials and config files.

**Decision**: Use `CURSOR_CONFIG_DIR` as the environment variable name, matching the pattern in CursorCli.md §5.
