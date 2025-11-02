# Agent Instructions

## Retrieving GitHub Action Status

The script `tools/get_action_status.py` can be used to retrieve the latest GitHub Action workflow results for a particular branch with detailed error information.

**Usage:**

To run the script:
```bash
python tools/get_action_status.py [--branch <branch-name>]
```

--branch <branch-name> should be explicitly specified when git is in a detached HEAD state as branch auto-detection will no longer work.

## Active Technologies
- Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, RabbitMQ 3.x (broker & result backend), PostgreSQL (existing MoonMind DB for run persistence), Codex CLI, GitHub CLI (001-celery-chain-workflow)
- PostgreSQL `spec_workflow_runs` + `spec_workflow_task_states`; RabbitMQ broker/result backend for task dispatch & state callbacks; object storage optional for large artifacts (initially local filesystem under `var/artifacts/spec_workflows/<run_id>`) (001-celery-chain-workflow)

## Recent Changes
- 001-celery-chain-workflow: Added Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, Redis 8 (broker), PostgreSQL (existing MoonMind DB for run persistence), Codex CLI, GitHub CLI
