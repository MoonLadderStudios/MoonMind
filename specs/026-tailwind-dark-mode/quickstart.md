# Quickstart: Tailwind Style System Phase 3 Dark Mode

## Prerequisites

- Node.js 18+ with npm installed
- Python 3.11 environment for MoonMind services
- Existing dashboard Tailwind toolchain dependencies installed (`npm install`)

## Build Dashboard CSS

```bash
cd /home/nsticco/MoonMind
npm install
npm run dashboard:css:min
```

## Start the API Service for Manual Verification

```bash
cd /home/nsticco/MoonMind
poetry run uvicorn api_service.main:app --reload
```

Open these routes in a browser:

- `http://localhost:8000/tasks`
- `http://localhost:8000/tasks/queue`
- `http://localhost:8000/tasks/orchestrator`

## Phase 3 Verification Flow

1. Confirm the dashboard masthead includes a theme toggle control.
2. Toggle between light and dark and verify visual updates apply immediately.
3. Reload each route and confirm selected theme persists.
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
cd /home/nsticco/MoonMind
node tests/task_dashboard/test_theme_runtime.js
./tools/test_unit.sh
```

## Output Integrity Check

After any CSS edits:

```bash
cd /home/nsticco/MoonMind
npm run dashboard:css:min
git diff -- api_service/static/task_dashboard/dashboard.css
```

Ensure generated output changes are scoped to intended Phase 3 theme updates.

## Execution Log (2026-02-19)

- Foundational CSS rebuild: `npm run dashboard:css:min` ✅ PASS
- Foundational unit validation: `./tools/test_unit.sh` ✅ PASS (`573 passed`)
- Theme runtime smoke validation: `node tests/task_dashboard/test_theme_runtime.js` ✅ PASS
- No-flash protocol validation: `testNoFlashMatrixProtocol` (20 system-light + 20 system-dark, unset preference) ✅ PASS (`40/40` passing; threshold `>=38/40`)
- Dark readability/accent automated QA: `testDarkTokenAndReadabilitySurfaces` + `testAccentHierarchyRules` ✅ PASS
- Final release-gate rerun:
  - `npm run dashboard:css:min` ✅ PASS
  - `node tests/task_dashboard/test_theme_runtime.js` ✅ PASS
  - `./tools/test_unit.sh` ✅ PASS (`573 passed`)
