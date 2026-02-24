# Agent Instructions — Skills-First Workflow Docs

## Scope
These instructions apply to all files under `specs/002-document-speckit-automation/`.

## Editing Guidelines
- Keep documentation aligned with the live implementation in `moonmind/workflows/speckit_celery/` and `docs/SpecKitAutomation.md`.
- Keep phase terminology compatible with legacy `speckit_*` values while documenting skills-first metadata (`selected_skill`, `execution_path`).
- When updating operational guidance, include concrete commands or API paths that have been validated against the repository.
- Preserve checklists and task IDs in `tasks.md`; mark items complete using `[x]` only after the corresponding work is merged.

## Operations & Monitoring Quick Reference
- Start the local stack with `docker compose up rabbitmq celery-worker api`; the API container handles Alembic migrations automatically.
- Export secrets (`GITHUB_TOKEN`, `CODEX_API_KEY`) plus optional StatsD settings (`WORKFLOW_METRICS_ENABLED`, `WORKFLOW_METRICS_HOST`, `WORKFLOW_METRICS_PORT`) before launching runs.
- Trigger a workflow via `asyncio.run(trigger_workflow_run(feature_key="002-document-speckit-automation"))` or the `/api/workflows/runs` endpoint.
- Monitor progress using `docker compose logs -f celery-worker`, StatsD metrics with the `workflow.*` prefix, and `curl http://localhost:5000/api/workflows/runs/<run_id>` for detailed status, artifacts, and skills-path metadata.
- Artifacts live at `/work/runs/<run_id>/artifacts`; use `docker compose cp` to retrieve logs or diff summaries. Clean up with `docker compose down` and `docker volume rm speckit_workspaces` when finished.
