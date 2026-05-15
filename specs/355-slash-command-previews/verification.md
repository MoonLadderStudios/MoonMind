# Verification Evidence: Provider-Neutral Slash Command Previews

## Automated Evidence

- Red-first frontend unit evidence:
  - `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts`
  - Initial failure: `draft.runtimeCommand` was `undefined` before edit/rerun metadata restoration was implemented.
- Red-first Python/API evidence:
  - `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts`
  - Initial failure: `runtimeCommandPreview` was missing from dashboard boot config.
  - `python -m pytest -q tests/integration/api/test_task_runtime_command_preview_boot_payload.py tests/e2e/test_task_create_submit_browser.py -q`
  - Initial failure: API integration raised `KeyError: 'runtimeCommandPreview'`.
- Focused frontend validation:
  - `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts`
  - Result: PASS, 44 passed and 229 skipped.
- Focused Python validation:
  - `./tools/test_unit.sh --python-only tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/tasks/test_task_contract.py`
  - Result: PASS, 142 passed.
- Focused API integration validation:
  - `python -m pytest -q tests/integration/api/test_task_runtime_command_preview_boot_payload.py tests/e2e/test_task_create_submit_browser.py -q`
  - Result: PASS for API integration; e2e module skipped unless `RUN_E2E_TESTS` is set.
- Focused browser validation:
  - `RUN_E2E_TESTS=1 python -m pytest -q tests/e2e/test_task_create_submit_browser.py::test_create_page_shows_runtime_command_previews -q`
  - Remediation setup: installed Python Playwright packages, installed Chromium, extracted missing Debian browser runtime libraries into `/tmp/mm-playwright-deps`, and rebuilt the stale Vite UI bundle with `./node_modules/.bin/vite build --config frontend/vite.config.ts && bash tools/run_repo_python.sh tools/verify_vite_manifest.py`.
  - Result: PASS.
- Full unit validation:
  - `./tools/test_unit.sh`
  - Result: PASS, Python 5072 passed, 1 xpassed, 16 subtests passed; frontend 351 passed and 229 skipped.

## Blocked Integration Evidence

- `./tools/test_integration.sh`
  - Blocked: Docker compose image build still fails with administrative `403 Forbidden` while building `repo-pytest`.

## Quickstart Scenario Coverage

- SC-001 `/review` preview on a slash-capable runtime: covered by frontend unit tests.
- SC-002 unknown `/foo` pass-through preview: covered by frontend unit tests.
- SC-003 unsupported runtime warning without text mutation: covered by frontend unit tests using `codex_cloud`.
- SC-004 escaped `\/review` literal text intent: covered by frontend unit tests.
- SC-005 mobile-safe, non-mutating preview states: covered by focused frontend unit and CSS review; live browser preview validation passes after rebuilding the Vite bundle for the managed runtime.
- SC-006 edit-mode metadata restoration: covered by `frontend/src/lib/temporalTaskEditing.test.ts`.
