# Quickstart: Task Presets Catalog

1. **Apply DB migration + seed defaults**
   ```bash
   poetry run alembic upgrade head
   ```
   Seeds under `api_service/data/task_step_templates/*.yaml` populate global templates.

2. **Run API service locally**
   ```bash
   docker compose up api rabbitmq postgres
   ```
   Ensure `FEATURE_FLAGS__TASK_TEMPLATE_CATALOG=1` (legacy fallback: `TASK_TEMPLATE_CATALOG=1`).

3. **List templates**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/task-step-templates?scope=global
   ```

4. **Expand a template**
   ```bash
   curl -X POST -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d '{"version":"1.0.0","inputs":{"change_summary":"Fix build"}}' \
        http://localhost:8000/api/task-step-templates/pr-code-change:expand
   ```

5. **Save steps as template**
   ```bash
   curl -X POST -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d @/tmp/save_template_payload.json \
        http://localhost:8000/api/task-step-templates/save-from-task
   ```

6. **UI validation**
   - Visit `/tasks/queue/new` → open "Add preset" drawer.
   - Apply template, choose Append/Replace, view preview + diff, collapse group.
   - Select steps, click "Save as template", confirm new entry appears in Personal scope.

7. **Tests**
   ```bash
   ./tools/test_unit.sh api_service/tests/test_task_step_templates.py
   ```
   Covers list/expand/save flows and UI serialization helpers.
