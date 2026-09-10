---
name: update-moonmind
description: Refresh MoonMind services from git by fetching and pinning a branch snapshot, pulling compose images, then restarting changed containers with optional orchestrator inclusion.
metadata:
  required-capabilities:
    - git
    - docker
    - python3
---

# Update MoonMind Deployment (default: without restarting orchestrator)

## Prerequisites

The host requires Git, Bash 4 or newer, Docker with the Compose V2 plugin, and
Python 3.10 or newer (`python3`). The access preflight uses only Python's standard
library; no project virtual environment or Python dependencies are required.
The script validates Python and Compose before fetching, changing the checkout,
or stopping services.

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
   - run the repository's standard-library-only `moonmind/deployment_access.py`
     deployment preflight against rendered Compose and installed API containers;
     stop before replacement if bindings or access settings drift, preserving
     the existing API until its deployment-owned configuration is reconciled;
     restore the pre-update checkout before resuming a quiesced worker when the
     gate fails, and leave that worker stopped if restoring the checkout fails
   - optionally `docker compose pull` while the resolver worker remains quiesced
     (unless `noComposePull` is set)
   - recreate the resolver worker only when it still exists in the post-checkout
     Compose topology
   - persist the exact checked-out git revision as the non-secret
     `MOONMIND_RUNTIME_SOURCE_REVISION` entry in the ignored project `.env`, so
     later ordinary `docker compose up -d` runs preserve the revision evidence
   - stamp each recreated application container with that revision and
     force-recreate any existing application process whose recorded revision is
     missing or stale, even when no new commit was fetched
   - detect files changed between pre-pull and post-pull commits, force-recreate only application processes affected by bind-mounted runtime source, and use normal Compose reconciliation for other selected services
   - restart services with image drift or stopped service state so runtime stays healthy
   - exclude the deployment-control worker from update targets so it can finish and verify the operation
4. By default, do not restart the `orchestrator` container, even when it changed.
