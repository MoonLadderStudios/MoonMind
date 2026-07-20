---
name: update-moonmind
description: Refresh MoonMind services from git by fetching and pinning a branch snapshot, pulling compose images, then restarting changed containers with optional orchestrator inclusion.
metadata:
  required-capabilities:
    - git
    - docker
---

# Update MoonMind Deployment (default: without restarting orchestrator)

## Inputs
- `repo` (optional): Path to the MoonMind git repository. Default `.`.
- `branch` (optional): Branch to update from. Default `main`.
- `allowDirty` / `allow_dirty` (optional): Allow running with uncommitted local git changes.
- `noComposePull` / `no_compose_pull` (optional): Skip pulling updated Docker images.
- `dryRun` / `dry_run` (optional): Print commands without executing them.
- `restartOrchestrator` / `restart_orchestrator` (optional): Restart the `orchestrator` container too.

## Workflow

1. Resolve the repository path.
2. Run `bash .agents/skills/update-moonmind/scripts/run-update-moonmind.sh --repo <path> --branch <branch>`.
   - Pass `--restart-orchestrator` if you need the orchestrator container restarted as well.
3. The script will:
   - validate `branch` as a safe git branch value
   - `git fetch` `<branch>` from `origin`
   - quiesce and coherently recreate the agent-runtime worker across changes to
     the live-mounted Skill catalog or its resolver code
   - checkout/reset local `<branch>` to the exact commit captured by that fetch
   - optionally `docker compose pull` while the resolver worker remains quiesced
     (unless `noComposePull` is set)
   - recreate the resolver worker only when it still exists in the post-checkout
     Compose topology
   - detect files changed between pre-pull and post-pull commits, force-recreate only application processes affected by bind-mounted runtime source, and use normal Compose reconciliation for other selected services
   - restart services with image drift or stopped service state so runtime stays healthy
   - exclude the deployment-control worker from update targets so it can finish and verify the operation
4. By default, do not restart the `orchestrator` container, even when it changed.
