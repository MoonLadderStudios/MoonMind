---
name: update-moonmind
description: Refresh MoonMind services from git by checking out a branch, pulling updates, pulling compose images, then restarting changed containers while skipping orchestrator.
---

# Update MoonMind Deployment (without restarting orchestrator)

## Inputs
- `repo` (optional): Path to the MoonMind git repository. Default `.`.
- `branch` (optional): Branch to update from. Default `main`.
- `allowDirty` / `allow_dirty` (optional): Allow running with uncommitted local git changes.
- `noComposePull` / `no_compose_pull` (optional): Skip pulling updated Docker images.
- `dryRun` / `dry_run` (optional): Print commands without executing them.

## Workflow

1. Resolve the repository path.
2. Run `bash .agents/skills/update-moonmind/scripts/run-update-moonmind.sh --repo <path> --branch <branch>`.
3. The script will:
   - validate `branch` as a safe git branch value
   - `git fetch` from `origin` and checkout/reset local `<branch>` from `origin/<branch>`
   - `git pull --ff-only origin <branch>`
   - optionally `docker compose pull` (unless `noComposePull` is set)
   - detect files changed between pre-pull and post-pull commits and restart only those changed containers
4. Do not restart the `orchestrator` container, even when it changed.
