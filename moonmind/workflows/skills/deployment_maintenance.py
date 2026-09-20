"""Report release-job state using the existing deployment maintenance fleet."""

from __future__ import annotations

import json
import logging

from moonmind.workflows.skills.deployment_execution import (
    DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS,
)
from moonmind.workflows.skills.deployment_release import (
    docker,
    inspect_owned,
    state_root,
    write_record,
)

logger = logging.getLogger(__name__)


def _deployment_project():
    """The Compose project this deployment owns.

    Deployment services are configured with MOONMIND_DEPLOYMENT_PROJECT_NAME,
    which the host updater also passes; COMPOSE_PROJECT_NAME is not set here.
    Reading the wrong variable silently fell back to "moonmind", so a
    deployment installed under a custom --compose-project would have ignored
    its own containers and reported another deployment's instead.
    """
    import os

    return os.environ.get("MOONMIND_DEPLOYMENT_PROJECT_NAME") or "moonmind"

# Containers a pre-recreate-in-place release or availability owner created
# beside the installed fleet. They reuse the deployment's own Compose project
# and service labels, so `docker compose ps -q <service>` returns more than one
# id while any of them exist, which blocks later updates.
_LEGACY_COHORT_PREFIXES = ("mm-candidate-", "mm-retained-")


async def observed_legacy_cohorts(project="moonmind"):
    """Names of this deployment's leftover blue/green cohort containers.

    Recreate-in-place never creates these. A deployment upgrading across that
    change can still be carrying some, and they are worth surfacing because
    they run an older image against the same Temporal task queue -- the
    mixed-version condition behind MoonLadderStudios/MoonMind#4363.

    They do not block an update. They are `compose run` one-offs, and
    `docker compose ps -q <service>` excludes one-offs, so installed-fleet
    verification never counts them. This is reported as retained-work
    evidence, not as a blocker, and nothing here removes them: a cohort may
    still hold the only poller for pinned or in-flight work, and no proof
    available to this pass distinguishes that safely.

    Scoped to this deployment's Compose project. One host can run several
    independent MoonMind deployments, and a daemon-wide listing would name
    another deployment's cohorts as blocking this one -- sending the operator
    to force-remove containers that may be its last pollers.
    """
    listed = await docker(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.Names}}",
    )
    return [
        name
        for name in (line.strip() for line in listed.splitlines())
        if name.startswith(_LEGACY_COHORT_PREFIXES)
    ]


async def reconcile_release(directory):
    """Report one release job and drop its finished updater container.

    Interrupted releases are not relaunched from here. The updater runs from
    the image its request pinned, so relaunching a job authored before
    recreate-in-place would execute the removed blue/green controller against
    the installed fleet. Re-running `./tools/update-moonmind.sh` starts a
    fresh, audited release instead, which is both simpler and the only path
    that cannot resurrect a deleted controller.
    """
    request = json.loads((directory / "request.json").read_text())
    owner = request["authored"]["owner"]
    result = {"job": directory.name, "resumed": False, "retired": [], "pending": []}
    updater = f"moonmind-release-update-{directory.name}"
    observed = await inspect_owned(updater, owner)
    if observed and observed["Image"] != request["imageId"]:
        raise ValueError("Release updater image differs from its durable owner")
    terminal = (directory / "result.json").exists()
    if not terminal:
        result["pending"].append(
            "running" if observed and observed["State"]["Running"] else "unfinished"
        )
    if observed and not observed["State"]["Running"] and terminal:
        await docker("rm", updater)
    return result


async def reconcile_releases():
    """A bounded maintenance pass that reports; it never relaunches or deletes.

    Recreate-in-place has no routing to reconcile and no cohort to retire, so
    this pass needs neither Temporal nor the Compose runner. It records each
    job's state, drops updater containers whose job is terminal, and reports
    any leftover blue/green cohort containers for an operator to remove.
    """
    import asyncio
    import fcntl

    from moonmind.utils.logging import redact_sensitive_text

    root = state_root()
    if not root.exists():
        return {"jobs": [], "errors": [], "legacyCohorts": []}
    result = {
        "jobs": [],
        "errors": [],
        "legacyCohorts": await observed_legacy_cohorts(_deployment_project()),
    }
    # Visit least-recently reconciled jobs first so one job cannot starve the
    # rest. Each pass is bounded.
    requests = sorted(
        (path.parent for path in root.glob("*/request.json")),
        key=lambda directory: (
            (directory / "maintenance.json").stat().st_mtime
            if (directory / "maintenance.json").exists()
            else 0
        ),
    )
    async with asyncio.timeout(DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS):
        for directory in requests[:20]:
            with (directory / "owner.lock").open("a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    outcome = await reconcile_release(directory)
                    result["jobs"].append(outcome)
                    write_record(directory / "maintenance.json", outcome)
                except Exception as exc:
                    error = {
                        "job": directory.name,
                        "error": redact_sensitive_text(str(exc))[:500],
                    }
                    result["errors"].append(error)
                    write_record(directory / "maintenance.json", error)
    if result["legacyCohorts"]:
        logger.info(
            "Leftover blue/green cohort containers are still running an older "
            "image against this deployment's task queues. They do not block "
            "updates; remove one only when its version is known to hold no "
            "pinned or in-flight work: %s",
            ", ".join(result["legacyCohorts"]),
        )
    return result
