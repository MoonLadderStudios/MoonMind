# Implementation Plan: codex-session-send-turn-hardening

**Branch**: `146-codex-session-send-turn-hardening` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/146-codex-session-send-turn-hardening/spec.md`

## Summary

Harden the Codex managed-session `send_turn` seam so the container-side runtime can complete a turn inside the same invocation that started it, avoiding the broken fresh-process `thread/resume` recovery path that currently wedges `MoonMind.AgentRun`. Preserve the launch-time vendor thread path hint in the persisted runtime state so later recovery still has the best available resume metadata.

## Technical Context

**Language/Version**: Python 3.13  
**Primary Dependencies**: `moonmind.workflows.temporal.runtime.codex_session_runtime`, `moonmind.workflows.temporal.runtime.managed_session_controller`, `moonmind.schemas.managed_session_models`  
**Storage**: managed-session JSON state file in the mounted session workspace  
**Testing**: focused pytest suites plus final verification via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind Temporal workers and managed Codex session containers  
**Project Type**: backend runtime/controller hardening  
**Constraints**: preserve the existing typed managed-session contracts; do not add compatibility wrappers or new transport dependencies

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The fix hardens orchestration around the existing Codex app-server session boundary.
- **II. One-Click Agent Deployment**: PASS. No new deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The change stays isolated inside the Codex-specific managed-session runtime/controller boundary.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main change aligns runtime behavior with the existing terminal-response contract already expected by the adapter.
- **VIII. Modular and Extensible Architecture**: PASS. The fix stays within the runtime and controller modules plus their boundary tests.
- **IX. Resilient by Default**: PASS. The change removes a reproducible stuck first-turn failure and adds regression coverage for the broken recovery seam.
- **XI. Spec-Driven Development**: PASS. This feature package scopes the hardening work before implementation.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The fix strengthens the existing path instead of layering a compatibility alias over the broken behavior.

## Research

- `CodexSessionAdapter.start()` already expects `agent_runtime.send_turn` to return a terminal `completed` response for managed Codex turns.
- `DockerCodexManagedSessionController.send_turn()` only polls `session_status` when the runtime returns `accepted` or `running`.
- `CodexManagedSessionRuntime.send_turn()` currently starts a turn and returns `running`, forcing terminal completion through a separate fresh-process `session_status` call.
- The session runtime already has `_wait_for_turn_completion()` and the state/finalization helpers needed to complete the turn within the same invocation.
- `launch_session()` currently discards the `thread/start` path when the file is not yet present on disk, even though later recovery can still benefit from that path hint.

## Project Structure

- Patch `moonmind/workflows/temporal/runtime/codex_session_runtime.py`.
- Update runtime/controller regression tests in:
  - `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
  - `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## Implementation Plan

1. Add or update failing tests that capture terminal in-process turn completion and launch-time thread-path persistence.
2. Update the runtime launch path to preserve the normalized vendor thread path from `thread/start`.
3. Refactor the runtime turn-wait helper so `send_turn` can complete/finalize terminal outcomes in-process and return them directly.
4. Keep controller-side polling only for genuinely non-terminal responses.
5. Run focused tests, then `./tools/test_unit.sh`.

## Verification Plan

### Automated Tests

1. `./.venv/bin/pytest -q tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`
2. `./tools/test_unit.sh`
