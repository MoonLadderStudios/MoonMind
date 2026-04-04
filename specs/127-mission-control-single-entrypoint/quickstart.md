# Quickstart: mission-control-single-entrypoint

1. Build the frontend bundle:

   ```bash
   npm run ui:build:check
   ```

2. Verify the shared Mission Control entrypoint routes correctly in targeted tests:

   ```bash
   npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx
   ./tools/test_unit.sh --python-only --no-xdist \
     tests/api_service/test_ui_assets.py \
     tests/api_service/test_ui_assets_strict.py \
     tests/api_service/test_task_dashboard_ui_assets_errors.py \
     tests/unit/api/routers/test_task_dashboard.py \
     tests/tools/test_verify_vite_manifest.py
   ```

3. Run the full unit suite required by repo policy:

   ```bash
   ./tools/test_unit.sh
   ```

4. Optional local dev check with FastAPI-backed HMR:

   ```bash
   npm run ui:dev
   MOONMIND_UI_DEV_SERVER_URL=http://127.0.0.1:5173 <fastapi-start-command>
   ```

   Open any `/tasks/*` Mission Control route and confirm the browser loads `/entrypoints/mission-control.tsx`, while the page content still follows `payload.page`.
