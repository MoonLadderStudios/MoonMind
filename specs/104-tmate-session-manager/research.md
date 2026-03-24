# Research: TmateSessionManager

**Feature**: 104-tmate-session-manager
**Date**: 2026-03-24

## Existing Tmate Implementations

### Decision: Extract shared class from launcher.py inline logic
**Rationale**: `launcher.py` (lines 426-527) contains the most complete tmate lifecycle implementation: socket creation, config file generation, subprocess launch with `tmate -S <sock> -f <conf> -F new-session`, readiness via `tmate -S <sock> wait tmate-ready`, and endpoint extraction for all 4 types (attach_ro, attach_rw, web_ro, web_rw). This is the reference implementation.
**Alternatives considered**: Starting from the OAuth activities Docker-exec approach was rejected because it's more constrained (runs inside a container, uses polling instead of `wait tmate-ready`).

### Decision: Use `asyncio.create_subprocess_exec` for tmate commands
**Rationale**: Both existing implementations use asyncio subprocess. The manager must be async to support `wait_for` timeouts on readiness and endpoint extraction.
**Alternatives considered**: Sync subprocess was rejected because the launcher's async context requires non-blocking I/O.

### Decision: `TmateServerConfig.from_env()` factory method
**Rationale**: Environment variables (`MOONMIND_TMATE_SERVER_*`) are already documented in `.env-template`. A factory method keeps config extraction self-contained.
**Alternatives considered**: Injecting config via MoonMind settings was acceptable but adds coupling; the factory method is simpler and matches the existing pattern.

### Decision: OAuth activities keep Docker-exec pattern but use shared constants
**Rationale**: OAuth tmate runs inside a Docker container — the activity can't directly manage the tmate subprocess. However, using the same endpoint key names and extraction command format (`tmate -S <sock> display -p '#{<key>}'`) ensures consistency.
**Alternatives considered**: Having the container entrypoint use `TmateSessionManager` internally and write endpoints to a file was considered but deferred (adds container image complexity).

## Socket Directory Strategy

### Decision: `/tmp/moonmind/tmate/` as default socket directory
**Rationale**: Already used by launcher.py. Scoped to avoid conflicts with system temp files.
**Alternatives considered**: Per-session directories were considered but add cleanup complexity without benefit since socket names are already unique per session.

## Exit Code Capture

### Decision: Preserve `exit_code_capture` parameter and `MM_EXIT_FILE` pattern
**Rationale**: The supervisor reads the exit code file after the tmate process ends to determine agent success/failure. This is an important behavioral contract.
**Alternatives considered**: Using tmate exit hooks was considered but is less portable.
