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

# Release-owned services: every Compose service whose image follows the
# requested release image (``api`` plus the versioned worker fleet).
# Handoff and apply layers must target these names: the canonical
# ``docker-compose.yaml`` has no service named ``worker``; the application
# workers are the ``temporal-worker-*`` fleet below.
RELEASE_OWNED_SERVICES = (
    "api",
    "temporal-worker-workflow",
    "temporal-worker-artifacts",
    "temporal-worker-llm",
    "temporal-worker-sandbox",
    "temporal-worker-agent-runtime",
    "temporal-worker-integrations",
    "temporal-worker-deployment-control",
)

DEPLOYMENT_WORKER_SERVICE = "temporal-worker-deployment-control"

# Environment variables through which the requested release image reaches
# Compose interpolation. Most release services use ``MOONMIND_IMAGE``; the
# deployment-control worker honors its own variable with ``MOONMIND_IMAGE``
# as fallback, so a pinned worker image must be exported explicitly.
RELEASE_IMAGE_ENV_VAR = "MOONMIND_IMAGE"
DEPLOYMENT_WORKER_IMAGE_ENV_VAR = "MOONMIND_DEPLOYMENT_WORKER_IMAGE"

PS_TIMEOUT_SECONDS = 120


def project_args(
    *,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Compose scoping flags for the wired target project.

    Empty values are omitted so unit fakes and tests without a deployment
    keep the historical bare commands. Scoping always precedes the
    subcommand (``docker compose <scope> pull ...``).
    """
    args: list[str] = []
    if project and str(project).strip():
        args.extend(["--project-name", str(project).strip()])
    if project_directory and str(project_directory).strip():
        args.extend(["--project-directory", str(project_directory).strip()])
    for item in compose_files or ():
        if item and str(item).strip():
            args.extend(["-f", str(item).strip()])
    return tuple(args)


def _scoped(
    base: tuple[str, ...],
    *,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    scope = project_args(
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    return (*base[:2], *scope, *base[2:])


def image_env(
    *,
    target_image: str | None = None,
    concrete_images: dict | None = None,
) -> dict[str, str]:
    """Environment mapping that applies requested images to Compose runs.

    ``target_image`` flows through ``MOONMIND_IMAGE`` (the variable every
    release service interpolates); a pinned deployment-worker image flows
    through its own variable. Empty when nothing was requested.
    """
    env: dict[str, str] = {}
    if target_image and str(target_image).strip():
        env[RELEASE_IMAGE_ENV_VAR] = str(target_image).strip()
    worker_image = (concrete_images or {}).get(DEPLOYMENT_WORKER_SERVICE)
    if worker_image and str(worker_image).strip():
        env[DEPLOYMENT_WORKER_IMAGE_ENV_VAR] = str(worker_image).strip()
    return env

PULL_COMMAND = ("docker", "compose", "pull", "--policy", "always", "--ignore-buildable")

# Semantic default: images are already staged by the pull step, so apply
# never reaches back to the registry and never builds.
UP_BASE = ("docker", "compose", "up", "-d", "--pull", "never", "--no-build")

# Explicit repair only. Never appended by the default apply path.
DOWN_COMMAND = ("docker", "compose", "down", "--remove-orphans")


def build_pull_command(
    services: tuple[str, ...] = (),
    *,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Stage every image needed for the requested update before apply."""
    scoped = _scoped(
        PULL_COMMAND,
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    return (*scoped, *services)


def build_up_command(
    services: tuple[str, ...] = (),
    *,
    force_recreate: bool = False,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Recreate changed services; ``force_recreate`` is explicit repair only."""
    args = [*UP_BASE]
    if force_recreate:
        args.append("--force-recreate")
    args.extend(["--remove-orphans", "--wait"])
    scoped = _scoped(
        tuple(args),
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    return (*scoped, *services)


def build_ps_command(
    services: tuple[str, ...] = (),
    *,
    project: str | None = None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Observe service liveness for post-apply verification (read-only)."""
    base = ("docker", "compose", "ps", "--format", "json")
    scoped = _scoped(
        base,
        project=project,
        project_directory=project_directory,
        compose_files=compose_files,
    )
    return (*scoped, *services)


def build_explicit_down_command() -> tuple[str, ...]:
    """Full teardown for explicit repair. No volumes or images are pruned."""
    return DOWN_COMMAND
