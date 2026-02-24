---
name: update-moonmind
description: Refresh MoonMind services from git by checking out a branch, pulling updates, pulling compose images, then restarting changed containers while skipping orchestrator.
---

# Update MoonMind Deployment (without restarting orchestrator)

## Inputs
- `repo` (optional): Path to the MoonMind git repository. Default `.`.
- `branch` (optional): Branch to update from. Default `main`.

## Workflow

1. Resolve the repository path.
2. Run `bash .agents/skills/update-moonmind/scripts/run-update-moonmind.sh --repo <path> --branch <branch>`.
3. The script will:
   - `git checkout <branch>`
   - `git pull`
   - `docker compose pull`
   - detect files changed by that pull and restart only those changed containers
4. Do not restart the `orchestrator` container, even when it changed.
