# Quickstart — Task Proposal Targeting Policy

1. **Set global policy knobs**
   - Add to `.env` or deployment manifests:
     ```bash
     export MOONMIND_PROPOSAL_TARGETS=both
     export MOONMIND_CI_REPOSITORY=MoonLadderStudios/MoonMind
     ```
   - Optional overrides per task run go inside canonical job payloads under `task.proposalPolicy`:
     ```json
     {
       "task": {
         "proposalPolicy": {
           "targets": ["project", "moonmind"],
           "maxItems": {"project": 2, "moonmind": 1},
           "minSeverityForMoonMind": "high"
         }
       }
     }
     ```

2. **Deploy updated services**
   - Restart the FastAPI API (`docker compose up api`) so it picks up new `settings.task_proposals` fields and schema validation.
   - Restart Codex workers (or Celery tasks invoking them) so `CodexWorkerConfig.from_env` reads the new env vars.

3. **Generate proposals during a run**
   - Ensure workers write `task_proposals.json` artifacts with `signal` metadata for any MoonMind-targeted suggestions.
   - After a successful run, the worker reads the policy, emits project + MoonMind proposals while respecting `maxItems`, and logs skipped entries when severity thresholds are not met.

4. **Review in dashboard**
   - Open `/tasks/proposals` and filter by the new repository/category/tag controls to see MoonMind CI proposals grouped under `[run_quality]`.
   - Promotion flow remains unchanged: reviewers promote via `/api/proposals/{id}/promote` and the existing job creation process runs.

5. **Validate**
   - Run `./tools/test_unit.sh` to cover FastAPI, worker, and config tests.
   - Smoke-test by submitting a synthetic MoonMind proposal payload through the API (with `repository=MoonLadderStudios/MoonMind`) to confirm metadata validation + priority derivation.
   - Rebuild dashboard assets via `npm run dashboard:css` before validating the updated filters, badges, and override-tooltips in `/tasks/proposals`.
