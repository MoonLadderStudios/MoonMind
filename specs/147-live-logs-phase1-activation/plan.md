# Implementation Plan: Live Logs Phase 1 Activation

**Branch**: `147-live-logs-phase1-activation` | **Date**: 2026-04-10 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/147-live-logs-phase1-activation/spec.md`

## Summary

The local repo already ships the session-aware event schema, the task-run observability router, spool-based live transport, and the managed-session controller/supervisor event publisher. The remaining Phase 1 gap is the Codex adapter path: `CodexSessionAdapter.start()` persists the task-run record only after `send_turn`, summary fetch, and artifact publication complete, and it hardcodes `liveStreamCapable=False`. This slice will activate the existing Live Logs path by persisting an early `running` record with live capability enabled, preserving bounded session snapshot fields, and updating failure/completion paths so Mission Control can attach to an active Codex turn without a frontend rewrite.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic, pytest, Temporal-adjacent runtime adapters already in `moonmind/`  
**Storage**: JSON file-backed managed run/session stores plus workspace-backed spool files and artifact-backed JSONL history  
**Testing**: `pytest` via `./tools/test_unit.sh`  
**Target Platform**: Linux containers and local dev environments backing managed-agent execution  
**Project Type**: backend/runtime observability slice  
**Performance Goals**: Preserve current live attachment behavior with no extra blocking round-trips before Mission Control can attach  
**Constraints**: No docs-only solution; keep provider behavior MoonMind-normalized; do not regress active/terminal observability truthfulness; preserve in-flight runtime control even when observability publication fails  
**Scale/Scope**: Narrow Phase 1 backend activation slice across the Codex adapter, managed-run persistence, and regression tests

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. This slice activates the existing MoonMind observability path rather than building a provider-native browser integration.
- **II. One-Click Agent Deployment**: PASS. No new operator setup or external infrastructure is required.
- **III. Avoid Vendor Lock-In**: PASS. The implementation stays inside MoonMind’s normalized task-run observability contract even though the immediate producer is Codex managed sessions.
- **IV. Own Your Data**: PASS. Live and historical observability remain workspace/artifact-backed and MoonMind-owned.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent-skill storage or runtime materialization changes are introduced.
- **VI. Design for Deletion / Tests as Anchor**: PASS. The change is a thin adapter/runtime fix guarded by boundary tests.
- **VII. Powerful Runtime Configurability**: PASS. Existing runtime config and observability routing stay intact.
- **VIII. Modular and Extensible Architecture**: PASS. The work stays within adapter and observability boundaries already defined by the runtime model.
- **IX. Resilient by Default**: PASS. The plan adds explicit failure-path cleanup for the new early-persistence window and keeps observability failures non-fatal to runtime control.
- **X. Facilitate Continuous Improvement**: PASS. The slice improves live operator diagnosis without changing run-summary semantics.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The implementation is driven by this spec/plan/tasks slice.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs remain unchanged; this execution slice lives under `specs/`.
- **XIII. Pre-Release, Remove Old Patterns Entirely**: PASS. No compatibility aliases or translation layers are added; the adapter’s incorrect late-only persistence behavior is replaced directly.

## Project Structure

### Documentation (this feature)

```text
specs/147-live-logs-phase1-activation/
├── plan.md
├── spec.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/adapters/codex_session_adapter.py
├── workflows/temporal/runtime/managed_session_controller.py
└── schemas/agent_runtime_models.py

api_service/
└── api/routers/task_runs.py

tests/
├── unit/workflows/adapters/test_codex_session_adapter.py
├── unit/services/temporal/runtime/test_managed_session_controller.py
└── unit/api/routers/test_task_runs.py
```

**Structure Decision**: Keep the implementation inside the existing adapter/controller/router seams. No new frontend or documentation work is required for this slice because the current observability consumers already exist.

## Implementation Strategy

### Baseline facts from current code

- `CodexSessionAdapter.start()` persists the task-run `ManagedRunRecord` only after `send_turn`, summary fetch, and artifact publication, so the UI cannot attach to an active Codex managed-session run.
- The same adapter currently writes `liveStreamCapable=False`, which causes `/observability-summary` and `/logs/stream` to report the run as unavailable for live follow.
- The managed-session controller and supervisor already emit normalized session events (`session_started`, `turn_started`, `turn_completed`, publication rows, reset-boundary rows) into the task-run spool and durable observability journal.
- The task-run router already prefers structured history, degrades through spool/merged artifacts, and truthfully reports live state when the run record exists and advertises capability.

### Planned changes

1. Update `CodexSessionAdapter.start()` to persist a `running` managed-run record immediately after the managed session is ensured and the bounded locator is known.
2. Teach `_persist_managed_run_record()` to advertise live capability for active Codex managed-session runs and to preserve workspace/session fields through running, failed, and completed updates.
3. Add failure-path persistence so a turn failure after the early save moves the managed-run record to a terminal failure state instead of leaving it stale.
4. Confirm controller-emitted session events remain visible through the task-run observability stream during active execution and that final artifact publication still writes durable refs back onto the record.

### Test-first approach

- Start with adapter tests that assert the store contains a `running` live-capable record before the `send_turn` await completes.
- Add or update failure-path adapter tests so failed turns rewrite the early record to a terminal state.
- Add a controller or router regression test only where needed to prove active-session event rows still reach the task-run stream and summary remains truthful.

## Complexity Tracking

No constitution violations are expected for this slice.
