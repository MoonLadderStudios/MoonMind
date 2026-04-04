# Research: mission-control-single-entrypoint

## Current State

- FastAPI route rendering in `api_service/api/routers/task_dashboard.py` injects page-specific assets plus `dashboard-alerts`, even though the route shell already carries a `page` identifier in the boot payload.
- `api_service/templates/react_dashboard.html` includes both `dashboard-alerts-root` and `mission-control-root`, which creates two React boot paths for one page render.
- `frontend/vite.config.ts` defines one Rollup input per Mission Control page, and `tools/verify_vite_manifest.py` parses that list to prove the manifest has every page key.

## Decisions

- Use one shared Vite entrypoint named `mission-control` and keep `boot_payload.page` as the selector for the requested page.
- Keep existing page components as separate modules and lazy-load them so bundle splitting still happens without multiple boot entrypoints.
- Move the former dashboard-alerts logic into the shared app shell to eliminate the extra React root.
- Preserve wide-panel behavior by passing layout metadata through the boot payload instead of hard-coding panel wrappers per template render.
- Retain `tools/verify_vite_manifest.py` as a reduced smoke test for the shared entrypoint rather than deleting the check entirely.

## Rejected Alternatives

- Full SPA/client-side routing rewrite: rejected because the request explicitly keeps FastAPI routes and the boot payload contract.
- Keeping per-page entrypoints and only removing `dashboard-alerts`: rejected because it would leave the main backend/manifest complexity in place.
- Deleting manifest verification entirely: rejected because the project still benefits from a minimal build smoke test for the shared bundle.
