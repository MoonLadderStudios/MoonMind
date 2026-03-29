# Implementation Plan: Live Logs Phase 3

**Branch**: `112-live-logs-phase-3` | **Date**: 2026-03-29 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/112-live-logs-phase-3/spec.md`

## Summary

This phase implements Server-Sent Events (SSE) based live log streaming for active managed runs in MoonMind. Instead of relying on legacy terminal infrastructure (`tmate`), the system will provide an API endpoint `/api/task-runs/{id}/logs/stream` that fans out standard output, standard error, and system logs to connected subscribers, with sequence tracking to allow seamless reconnection.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI (for SSE via `sse-starlette` or similar), Redis/In-memory PubSub for fan-out.
**Storage**: N/A for live streaming beyond bounded memory buffers (durable artifacts are handled in Phase 1/2). 
**Testing**: `pytest`, async test clients.
**Target Platform**: Linux server/Docker container.
**Project Type**: Backend Services.
**Performance Goals**: <500ms latency from subprocess log generation to SSE delivery; support 50+ concurrent viewers per run.
**Constraints**: Must cleanly release resources upon client disconnect to prevent memory leaks. Must have clear fallback for ended runs.
**Scale/Scope**: Impacts all active managed agent runs observability.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: Pass. Uses standard API patterns to observe agent runs instead of tying into legacy tools.
- **III. Avoid Vendor Lock-In**: Pass. Relies on standard HTTP SSE transport layer over plain text.
- **IV. Own Your Data**: Pass. Live stream acts as a view over the raw artifacts.
- **VIII. Modular and Extensible Architecture**: Pass. The stream publisher is an isolated backend component that doesn't entangle with the core worker loop runtime.
- **IX. Resilient by Default**: Pass. Implements robust connection lifecycle management, `since=` query resume capabilities, and explicit `fallback` states.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: Pass. Modifies `009-LiveLogsPlan.md` which is in `docs/tmp`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: Pass. Prepares for full cleanup of the legacy terminal UI tools for managed runs.

## Project Structure

### Documentation (this feature)

```text
specs/112-live-logs-phase-3/
├── plan.md              
├── research.md          
├── data-model.md        
├── quickstart.md        
├── contracts/           
│   └── requirements-traceability.md
└── tasks.md             
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── task_runs.py       # Add GET /api/task-runs/{id}/logs/stream endpoint
moonmind/
└── services/
    └── observability/
        ├── publisher.py       # Memory/Redis channel fan-out logic
        ├── subscriber.py      # SSE generator logic
        └── models.py          # LogStreamEvent DTO
tests/
└── integration/
    └── api/
        └── test_live_logs.py  # SSE endpoint, reconnection, disconnect tests
```

**Structure Decision**: The live logging API falls under the standard FastAPI route hierarchy for `task_runs.py`. The core pub-sub logic will reside under a dedicated `observability` service namespace to keep streaming isolated from durable artifact generation.
