# Agent Instructions — Spec Kit Automation Docs

## Scope
These instructions apply to all files under `specs/002-document-speckit-automation/`.

## Editing Guidelines
- Keep documentation aligned with the live implementation in `moonmind/workflows/speckit_celery/` and `docs/SpecKitAutomation.md`.
- When updating operational guidance, include concrete commands or API paths that have been validated against the repository.
- Preserve checklists and task IDs in `tasks.md`; mark items complete using `[x]` only after the corresponding work is merged.

## Operations & Monitoring Quick Reference
- Start the local stack with `docker compose up rabbitmq celery-worker api`; the API container handles Alembic migrations automatically.
- Export secrets (`GITHUB_TOKEN`, `CODEX_API_KEY`) plus optional StatsD settings (`SPEC_WORKFLOW_METRICS_ENABLED`, `SPEC_WORKFLOW_METRICS_HOST`, `SPEC_WORKFLOW_METRICS_PORT`) before launching runs.
- Trigger a workflow via `asyncio.run(trigger_spec_workflow_run(feature_key="002-document-speckit-automation"))` or the `/api/spec-automation/runs` endpoint.
- Monitor progress using `docker compose logs -f celery-worker`, StatsD metrics with the `spec_automation.*` prefix, and `curl http://localhost:8080/api/spec-automation/runs/<run_id>` for detailed status and artifact IDs.
- Artifacts live at `/work/runs/<run_id>/artifacts`; use `docker compose cp` to retrieve logs or diff summaries. Clean up with `docker compose down` and `docker volume rm speckit_workspaces` when finished.
