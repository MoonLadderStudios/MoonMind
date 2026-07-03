# Gemini Guidance

Use `AGENTS.md` as the primary MoonMind repository guidance file before making changes.

`AGENTS.md` now contains MoonMind principles, documentation precedence, testing discipline, Temporal troubleshooting, security guardrails, compatibility policy, and shared skill runtime rules.

MoonMind project principles are no longer kept in a standalone constitution file.

## Active Technologies
- Python >=3.10,<3.14; TypeScript 5.9 with React 19. + FastAPI, Pydantic v2, Temporal workflow code, pytest, React, Vite, Vitest. (001-add-auto-publish)
- No database migration planned; publish evidence is a run artifact (`artifacts/publish_result.json`) consumed into finish summary/publish context. (001-add-auto-publish)

## Recent Changes
- 001-add-auto-publish: Added Python >=3.10,<3.14; TypeScript 5.9 with React 19. + FastAPI, Pydantic v2, Temporal workflow code, pytest, React, Vite, Vitest.
