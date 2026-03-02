# Quickstart – Tasks Image Attachments Phase 1

## 1. Prerequisites
- Python 3.11 + Poetry deps installed (`poetry install`), Node 18+ for Tailwind build.
- Postgres, RabbitMQ, and artifact volume configured as in `docker-compose.yaml` (or use `docker compose up rabbitmq celery-worker api` for the default stack).
- Ensure `AGENT_JOB_ATTACHMENT_ENABLED=true` and artifact root has ≥100MB free disk.
- Worker tokens issued via `/api/queue/worker-tokens` for the Codex worker.
- Optional: Set `MOONMIND_VISION_CONTEXT_ENABLED=false` to validate the “disabled” prompt path.

## 2. Build dashboard assets (one time per change)
```bash
npm install
npm run dashboard:css:min
```
This regenerates `api_service/static/task_dashboard/dashboard.css` after UI changes.

## 3. Create a job with attachments via API
```bash
REQUEST_PAYLOAD='{"type":"task","payload":{"repository":"Moon/Test","task":{"instructions":"Investigate layout"}}}'
http --form POST :8000/api/queue/jobs/with-attachments \
  Authorization:"Bearer <user_token>" \
  request="$REQUEST_PAYLOAD" \
  files@./tests/fixtures/attachments/placeholder.png \
  files@./tests/fixtures/attachments/placeholder.jpg
```
Expected: HTTP 201 with `attachments[*].name` values prefixed by `inputs/`. Use the PNG/JPEG/WebP fixtures under `tests/fixtures/attachments/` to keep examples deterministic.
Use `tests/fixtures/attachments/invalid-signature.png` for negative validation scenarios.

## 4. Claim the job on a worker and verify prepare outputs
```bash
moonmind-codex-worker --config ./config.toml --oneshot
```
After prepare completes:
- `repo/.moonmind/inputs/` contains downloaded files.
- `repo/.moonmind/attachments_manifest.json` lists ids/digests/local paths.
- `repo/.moonmind/vision/image_context.md` exists (or notes that vision context is disabled).
- `artifacts/task_context.json` now includes an `attachments` object with counts + manifest path.

## 5. Confirm prompt injection
Inspect the worker logs (`artifacts/logs/execute.log` or CLI stdout) and find the `INPUT ATTACHMENTS` block before the `WORKSPACE` section for Codex/Gemini/Claude runs. The block should inline the rendered Markdown and mention `.moonmind/inputs`.

## 6. Dashboard UX checks
- Navigate to `/tasks/queue/new`, drag/drop ≤10 PNG/JPEG/WebP files, and submit the form.
- Open the created job detail panel: thumbnails plus download buttons should appear, and unauthorized accounts should receive HTTP 403 from `/api/queue/jobs/<id>/attachments/...`.

## 7. Testing commands
- Unit tests (API, service, worker, dashboard view models):
  ```bash
  ./tools/test_unit.sh tests/unit/api/routers/test_agent_queue.py::test_create_job_with_attachments
  ./tools/test_unit.sh tests/unit/workflows/agent_queue/test_service_attachments.py
  ./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py::test_prepare_stage_downloads_attachments
  ```
- Runtime alignment validation (required task command):
  ```bash
  ./tools/test_unit.sh tests/unit/api/routers/test_agent_queue.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/agents/codex_worker/test_worker.py
  ```
  Result captured on 2026-03-01: `904 passed, 3714 warnings, 8 subtests passed`.
- Integration/worker happy-path (optional, longer):
  ```bash
  docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
  ```

## 8. Troubleshooting
- `413 attachment_too_large`: lower `AGENT_JOB_ATTACHMENT_MAX_BYTES` only in dev; production should stay at 10MB.
- Attachments missing on worker: ensure worker token has `capabilities` including `codex` and that the job is claimed (`status=RUNNING`).
- Vision context empty: check `MOONMIND_VISION_*` env vars and provider credentials; fallback text should still appear.
