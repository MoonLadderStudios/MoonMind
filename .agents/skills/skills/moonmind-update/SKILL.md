---
name: moonmind-update
description: Run the Moonmind repository refresh workflow by checking out `main` (or another branch), pulling latest git changes, pulling Docker Compose images, and executing the Moonmind update script (or equivalent command). Use when users ask to update Moonmind, refresh a deployment, or automate this sequence as a repeatable one-command operation.
---

# Moonmind Update Workflow

Execute an operationally safe refresh for a Moonmind deployment repository.

## Workflow

1. Resolve target repository path.
2. Run:
   `bash .agents/skills/skills/moonmind-update/scripts/run.sh --repo /path/to/repo`
3. Add overrides when needed:
   `--branch <name>` for non-`main` flow.
   `--update-command "<command>"` when update script is non-standard.
   `--allow-dirty` only when explicitly approved.
   `--no-compose-pull` to skip image pull.
   `--dry-run` to preview commands.
4. Stop on first failure and report the failing step.

## Update Script Resolution

Without `--update-command`, the script auto-detects one of:

- `./scripts/update-moonmind.sh`
- `./scripts/update_moonmind.sh`
- `./update-moonmind.sh`
- `./update_moonmind.sh`
- `./scripts/update.sh`
- `./update.sh`
