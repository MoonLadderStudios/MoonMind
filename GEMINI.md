# Gemini Guidance

Use `AGENTS.md` as the primary MoonMind repository guidance file before making changes.

`AGENTS.md` now contains MoonMind principles, documentation precedence, testing discipline, Temporal troubleshooting, security guardrails, compatibility policy, and shared skill runtime rules.

MoonMind project principles are no longer kept in a standalone constitution file.

## Active Technologies
- Python 3.11 project conventions; FastAPI control plane; SQLAlchemy async models and services. + FastAPI, Pydantic v2, SQLAlchemy async, Alembic migrations, pytest, pytest-asyncio, TestClient. (001-complete-checkpoint-branch-graph)
- PostgreSQL in deployment; SQLite-backed async sessions in focused unit tests; existing Alembic migration `api_service/migrations/versions/333_mm1088_checkpoint_branch.py`. (001-complete-checkpoint-branch-graph)

## Recent Changes
- 001-complete-checkpoint-branch-graph: Added Python 3.11 project conventions; FastAPI control plane; SQLAlchemy async models and services. + FastAPI, Pydantic v2, SQLAlchemy async, Alembic migrations, pytest, pytest-asyncio, TestClient.
