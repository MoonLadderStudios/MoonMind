# Quickstart: Tailwind Style System Phase 3 Dark Mode

## Prerequisites

- Node.js 18+ with npm installed
- Python 3.11 environment for MoonMind services
- Existing dashboard Tailwind toolchain dependencies installed (`npm install`)

## Build Dashboard CSS

```bash
# from repository root
npm install
npm run dashboard:css:min
```

## Start the API Service for Manual Verification

```bash
# from repository root
poetry run uvicorn api_service.main:app --reload
```

Open these routes in a browser:

- `http://localhost:8000/tasks`
- `http://localhost:8000/tasks/queue`
- `http://localhost:8000/tasks/orchestrator`

## Phase 3 Verification Flow

1. Confirm the dashboard shell renders and applies a resolved theme at first paint.
2. In browser devtools, set `localStorage.setItem("moonmind.theme", "dark")`, reload each route, and confirm dark mode persists.
3. Set `localStorage.setItem("moonmind.theme", "light")`, reload each route, and confirm light mode persists.
4. Clear `moonmind.theme` from local storage and verify default mode follows system preference.
5. With no saved preference, change system color scheme and verify dashboard updates accordingly.
6. Hard-refresh with light and dark system preferences and confirm no first-paint flash occurs.
7. In dark mode, validate readability of:
   - tables and row states
   - form fields and focus styles
   - `.queue-live-output`
8. Validate accent hierarchy:
   - purple remains primary for active/primary interactions
   - yellow/orange appears only in warning/high-attention contexts

## Automated Regression Checks

```bash
# from repository root
node tests/task_dashboard/test_theme_runtime.js
./tools/test_unit.sh
```

## Output Integrity Check

After any CSS edits:

```bash
# from repository root
npm run dashboard:css:min
git diff -- api_service/static/task_dashboard/dashboard.css
```

Ensure generated output changes are scoped to intended Phase 3 theme updates.

## Execution Log (2026-02-19)

- Foundational CSS rebuild: `npm run dashboard:css:min` ✅ PASS
- Foundational unit validation: `./tools/test_unit.sh` ✅ PASS (`573 passed`)
- Theme runtime smoke validation: `node tests/task_dashboard/test_theme_runtime.js` ✅ PASS
- No-flash protocol validation: `testNoFlashMatrixProtocol` executes the inline bootstrap script for 20 system-light + 20 system-dark runs (unset preference) ✅ PASS (`40/40` passing)
- Dark readability/accent automated QA: `testDarkTokenAndReadabilitySurfaces` + `testAccentHierarchyRules` ✅ PASS
- Final release-gate rerun:
  - `npm run dashboard:css:min` ✅ PASS
  - `node tests/task_dashboard/test_theme_runtime.js` ✅ PASS
  - `./tools/test_unit.sh` ✅ PASS (`573 passed`)
