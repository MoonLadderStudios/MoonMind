# Quickstart: Tailwind Style System Phase 2

## Prerequisites
- Node.js 18+ with npm
- Python 3.11 virtualenv for MoonMind services
- Tailwind/PostCSS dev dependencies installed via `npm install`

## Build & Watch CSS
```bash
npm install            # first run
npm run dashboard:css  # one-shot build from dashboard.tailwind.css
npm run dashboard:css:watch  # optional while iterating on tokens
```

## Run FastAPI dev server to preview
```bash
poetry run uvicorn api_service.main:app --reload
# Visit http://localhost:8000/tasks and verify purple palette across routes
```

## Validation Checklist
1. Capture before/after screenshots for `/tasks`, `/tasks/queue`, `/tasks/orchestrator`, and a detail page.
2. Confirm status chips show the documented colors (amber, cyan, violet, green, rose).
3. Run `./tools/test_unit.sh` to confirm router/template regressions are absent.
4. Ensure `docs/TailwindStyleSystem.md` stays in sync with any token tweaks.
5. Commit only generated `dashboard.css` outputs produced by `dashboard:css:min` to keep diffs deterministic.
