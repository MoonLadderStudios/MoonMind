# Implementation Plan: Provider Profiles Phase 5 — Terminal PTY Bridge and OAuthSession Completion

**Branch**: `113-provider-profiles-p5` | **Date**: 2026-03-28 | **Spec**: [spec.md](spec.md)

## Summary

Migrates OAuth session transport from legacy browser-based URL runners to a Terminal PTY bridge
model. Adds `terminal_session_id`, `terminal_bridge_id`, `connected_at`, and `disconnected_at`
columns to `managed_agent_oauth_sessions`, and renames the status enum value `oauth_runner_ready`
to `bridge_ready`. Updates workflow activities and Temporal activity catalog to align with the new
naming.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, SQLAlchemy, Temporalio, asyncio (subprocess)
**Storage**: PostgreSQL (managed_agent_oauth_sessions, oauthsessionstatus enum)
**Testing**: pytest
**Target Platform**: Linux server (Docker worker)
**Project Type**: Single web application

## Constitution Check

No violations. Pre-release project. Old activity `oauth_session.update_session_urls` fully replaced
by `oauth_session.update_terminal_session` in catalog, runtime, artifacts delegate, and workflow.

## Project Structure

```text
api_service/
├── db/models.py                            # OAuthSessionStatus: BRIDGE_READY replaces OAUTH_RUNNER_READY
├── api/routers/oauth_sessions.py           # URL fields removed from responses
├── api/schemas_oauth_sessions.py           # OAuthSessionResponse: url fields dropped
└── migrations/versions/7bd7130eae51_*.py  # Schema migration + enum ALTER TYPE

moonmind/workflows/temporal/
├── runtime/terminal_bridge.py             # start_terminal_bridge_container (docker stub)
├── activities/oauth_session_activities.py # update_terminal_session + start_auth_runner validation
├── workflows/oauth_session.py             # Updated to call update_terminal_session
├── activity_catalog.py                    # Catalog entry updated
├── activity_runtime.py                    # Runtime routing updated
└── artifacts.py                           # Delegate updated

specs/113-provider-profiles-p5/
└── data-model.md                          # OAuthSession terminal fields documented
```

## Key Decisions

- `oauth_runner_ready` enum value is preserved in PostgreSQL via `ADD VALUE IF NOT EXISTS` to avoid
  locking; rows are backfilled to `bridge_ready` in the same migration.
- `alpine:3.19` is pinned in the bridge container stub; a 30-second timeout wraps `communicate()`.
- `docker` CLI absence raises `RuntimeError` with a clear diagnostic message.
