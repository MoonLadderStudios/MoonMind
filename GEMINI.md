# Gemini Guidance

Use `AGENTS.md` as the primary MoonMind repository guidance file before making changes.

`AGENTS.md` now contains MoonMind principles, documentation precedence, testing discipline, Temporal troubleshooting, security guardrails, compatibility policy, and shared skill runtime rules.

MoonMind project principles are no longer kept in a standalone constitution file.

## Active Technologies
- Python >=3.10,<3.14; TypeScript 5.9 with React 19. + FastAPI, Pydantic v2, Temporal workflow code, pytest, React, Vite, Vitest. (001-add-auto-publish)
- No database migration planned; publish evidence is a run artifact (`artifacts/publish_result.json`) consumed into finish summary/publish context. (001-add-auto-publish)
- Python 3.11+ backend/workflow code; YAML preset templates; TypeScript frontend not expected for this story. + FastAPI service layer, SQLAlchemy preset catalog service, Temporal workflow/activity abstractions, pytest/pytest-asyncio, GitHub/Jira adapter services. (001-update-presets)
- Preset seed YAML under `api_service/data/presets/`; database-backed preset catalog in application runtime; workflow artifacts under local/runtime artifact paths such as `artifacts/...` and `var/artifacts/...`. (001-update-presets)

## Recent Changes
- 001-add-auto-publish: Added Python >=3.10,<3.14; TypeScript 5.9 with React 19. + FastAPI, Pydantic v2, Temporal workflow code, pytest, React, Vite, Vitest.
- 001-update-presets: Added Python 3.11+ backend/workflow code; YAML preset templates; TypeScript frontend not expected for this story. + FastAPI service layer, SQLAlchemy preset catalog service, Temporal workflow/activity abstractions, pytest/pytest-asyncio, GitHub/Jira adapter services.
