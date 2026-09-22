"""Compose command construction for the standalone controller.

Normal service replacement is image preparation/pull followed by
changed-service ``up``. Full ``down`` and force-recreate are explicit repair
operations only, never automatic escalation. No volume/image pruning exists
anywhere in this module.
"""

from __future__ import annotations

# Bounded so a hung daemon cannot stall an operation forever; callers pass
# these as subprocess timeouts.
PULL_TIMEOUT_SECONDS = 900
UP_TIMEOUT_SECONDS = 900

PULL_COMMAND = ("docker", "compose", "pull", "--policy", "always", "--ignore-buildable")

# Semantic default: images are already staged by the pull step, so apply
# never reaches back to the registry and never builds.
UP_BASE = ("docker", "compose", "up", "-d", "--pull", "never", "--no-build")

# Explicit repair only. Never appended by the default apply path.
DOWN_COMMAND = ("docker", "compose", "down", "--remove-orphans")


def build_pull_command(services: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Stage every image needed for the requested update before apply."""
    return (*PULL_COMMAND, *services)


def build_up_command(
    services: tuple[str, ...] = (),
    *,
    force_recreate: bool = False,
) -> tuple[str, ...]:
    """Recreate changed services; ``force_recreate`` is explicit repair only."""
    args = [*UP_BASE]
    if force_recreate:
        args.append("--force-recreate")
    args.extend(["--remove-orphans", "--wait"])
    return (*args, *services)


def build_explicit_down_command() -> tuple[str, ...]:
    """Full teardown for explicit repair. No volumes or images are pruned."""
    return DOWN_COMMAND
