# Quickstart: Task Proposal Queue Phase 2

1. **Enable feature flag**: set `ENABLE_TASK_PROPOSALS=true` (already Phase 1) plus new `ENABLE_TASK_PROPOSALS_PHASE2=true` to activate dedup, edit-before-promote, snooze, and notifications.
2. **Run DB migration**: `poetry run alembic upgrade head` to create new columns, enums, and notification table.
3. **Start API + dashboard**: `docker compose up api dashboard` ensures `/api/proposals` endpoints and UI share runtime config.
4. **Submit proposal**: use worker or `curl` example to post to `/api/proposals` with canonical payload; observe response fields `dedupHash`, `reviewPriority`, `similar`.
5. **Review via dashboard**: navigate to `/tasks/proposals`, open an item, and use "Edit & Promote" to tweak instructions before enqueuing.
6. **Snooze + priority**: from UI or CLI, hit `/api/proposals/{id}/snooze` and `/priority` endpoints; verify snoozed proposal disappears from default list until expiration.
7. **Notifications**: configure Slack/webhook secrets in `config.toml` and tail logs for `proposals.notifications.*` metrics when security/tests items arrive.
