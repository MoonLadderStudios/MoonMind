# Quickstart: Submit Runtime Selector

## Prerequisites
- Repository dependencies installed (`pip install -e .`, `npm install` if dashboard assets need rebuilding).
- Dashboard bundle rebuilt after JS changes: `npm run dashboard:js` (or the project’s existing watch/build script) so `api_service/static/task_dashboard/dashboard.js` reflects edits.
- MoonMind stack running: `docker compose up api rabbitmq celery-worker orchestrator` (or equivalent) so queue/orchestrator endpoints are reachable.
- Browser access to `https://localhost:8443/tasks/queue/new` and `/tasks/orchestrator/new`.

## 1. Run automated unit tests
```bash
./tools/test_unit.sh
```
- Executes pytest plus Node dashboard suites (including `tests/task_dashboard/test_submit_runtime.js`). All suites must pass before release.

## 2. (Optional) Tailwind/CSS rebuild
```bash
npm run dashboard:css:min
```
- Only needed if submit-form markup changes require new styles. Record gzip delta if CSS changes land together with this feature.

## 3. Verify worker runtime submission flow
1. Open `/tasks/queue/new` (shared form auto-selects the configured default runtime).
2. Confirm worker-only fields are visible: step editor, presets, repo/branch inputs, publish mode, priority, max attempts, propose tasks.
3. Fill the primary step instructions, repository, publish mode, and priority. Add a secondary step to ensure the editor still works.
4. Submit and watch the browser network tab: request must hit `/api/queue/jobs` with `type="task"` payload containing the selected runtime. Expect redirect to `/tasks/queue/{jobId}`.
5. Reload `/tasks/queue/new` and repeat with at least one non-default runtime (e.g., Gemini) to confirm runtime defaults swap model/effort placeholders.

## 4. Verify orchestrator submission flow
1. Navigate to `/tasks/orchestrator/new`; runtime selector should default to Orchestrator and hide worker-only fields.
2. Enter instruction, `targetService`, choose `priority=high`, optionally add an approval token, and submit.
3. Network tab must show `POST /orchestrator/runs` with `{ instruction, targetService, priority, approvalToken? }`. Expect redirect to `/tasks/orchestrator/{runId}`.
4. Clear instruction or target service and attempt to submit—inline error must mention the missing field and no network call should fire.

## 5. Validate runtime switching + draft preservation
1. On `/tasks/queue/new`, populate several worker fields (instructions, extra steps, repo override, publish mode, priority, template notes).
2. Switch runtime selector to Orchestrator. Verify worker fields hide but the shared instruction text remains populated.
3. Enter orchestrator-only values, then switch back to a worker runtime. Worker inputs must repopulate exactly as entered (steps, repo, publish mode, etc.). Switching again to Orchestrator should restore its draft as well.
4. Repeat the toggle ≥5 times to emulate the spec’s “five consecutive toggles” test.

## 6. Error-handling regression checks
- Worker flow: set repo to an invalid value (`foo`) and submit—expect repository validation message, no network requests.
- Worker flow: blank primary step instructions and submit—expect “Primary step instructions are required.”
- Orchestrator flow: choose runtime=orchestrator, leave priority blank, submit—form should coerce to `normal` rather than fail. Clear target service to verify error path.

## 7. Checklist / QA log template

| Date (UTC) | Scenario | Result | Notes |
| --- | --- | --- | --- |
| YYYY-MM-DD | Worker submit (Codex) | Pass/Fail | jobId + endpoint |
| YYYY-MM-DD | Worker submit (Gemini) | Pass/Fail | defaults swapped |
| YYYY-MM-DD | Orchestrator submit | Pass/Fail | runId + endpoint |
| YYYY-MM-DD | Runtime toggle stress (≥5 switches) | Pass/Fail | mention any flicker |
| YYYY-MM-DD | Validation (invalid repo + missing targetService) | Pass/Fail | expected error copy |

## 8. Troubleshooting tips
- Inspect `window.__submitRuntimeTest` helpers in dev tools to confirm runtime detection logic at run time (`determineSubmitDestination`, `validateOrchestratorSubmission`).
- If submissions fail, capture the JSON response body from the network tab; the UI surfaces `error.message`, so backend-provided `detail.message` should bubble through for debugging.
