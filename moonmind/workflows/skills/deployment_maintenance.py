"""Reconcile release ownership using the existing deployment maintenance fleet."""

from __future__ import annotations

import json
import time
from dataclasses import replace

from moonmind.workflows.skills.deployment_execution import (
    DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS,
)
from moonmind.workflows.skills.deployment_release import (
    _ensure_command_succeeded,
    docker,
    inspect_owned,
    launch_updater,
    readiness_matches,
    state_root,
    worker_readiness,
    write_record,
)

# Containers a pre-recreate-in-place release or availability owner created
# beside the installed fleet. They reuse the deployment's Compose project and
# service labels, so they must be excluded by name when asking whether the
# installed fleet itself is healthy.
_LEGACY_COHORT_PREFIXES = ("mm-candidate-", "mm-retained-")


async def installed_fleet_is_serving(runner, expected):
    """Whether the installed fleet alone serves *expected* on every fleet.

    A legacy cohort container carries the installed fleet's own Compose
    project and service labels, so ``docker compose ps -q <service>`` returns
    it alongside the real one. Counting both would make this check fail
    exactly while a leftover cohort exists, which is the only time it is
    asked -- so cohort-named containers are excluded and the remaining
    installed container must be unique and ready on *expected*.
    """
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

    for service in _FLEET_SERVICE_NAMES.values():
        # No requested_image: MOONMIND_IMAGE then resolves from the
        # deployment's own environment, which is the installed release.
        found = await runner._run_compose_command(
            ("docker", "compose", "ps", "-q", service)
        )
        _ensure_command_succeeded("inspect installed worker", found)
        installed = []
        for identifier in found["stdout"].split():
            rows = json.loads(await docker("inspect", identifier))
            name = str(rows[0].get("Name", "")).lstrip("/")
            if name.startswith(_LEGACY_COHORT_PREFIXES):
                continue
            installed.append(identifier)
        if len(installed) != 1:
            return False
        try:
            state = await worker_readiness(installed[0])
        except RuntimeError:
            return False
        if not readiness_matches(state, expected):
            return False
    return True


async def retire_legacy_cohort(directory, owner, kind):
    """Remove containers a pre-recreate-in-place release left behind.

    Blue/green promotion is gone, so nothing routes to a ``mm-candidate-*`` or
    ``mm-retained-*`` container any more and none are created. Deployments that
    upgrade across that change can still be carrying a cohort from an earlier
    release, so retire it once and record the receipt. Removal is bounded and
    owner-checked; a container owned by another job is never touched.
    """
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

    names = [
        f"mm-{kind}-{directory.name[:16]}-{fleet.replace('_', '-')}"
        for fleet in _FLEET_SERVICE_NAMES
    ]
    if kind == "candidate":
        names.append(f"mm-candidate-{directory.name[:16]}-api")
    for name in names:
        existing = await inspect_owned(name, owner)
        if not existing:
            continue
        if existing["State"]["Running"]:
            await docker("stop", "--time", "60", name)
        await docker("rm", name)


def _legacy_cohort_version(directory, kind, deployment):
    """The Temporal deployment version a legacy cohort served."""
    if kind == "candidate":
        routing = json.loads((directory / "routing.json").read_text())
        return f"{routing.get('deployment') or deployment}.{routing['candidate']}"
    return json.loads((directory / "retained.json").read_text())["version"]


async def retire_legacy_cohorts(directory, runner, client, owner, pending):
    """Retire this job's legacy cohorts once nothing can still be routed to them.

    Installed-fleet readiness alone is not sufficient. A retained cohort can
    still be the current route, or still serve pinned in-flight executions,
    while every newly installed container is ready -- stopping it then removes
    the only old-version pollers. Two independent proofs are required: the
    installed fleet is uniquely serving the running release, and Temporal
    agrees the cohort's own version is finished, either because it has drained
    or because it is the current version that the installed fleet now serves.
    """
    import os

    from moonmind.release_identity import installed_release
    from moonmind.workflows.temporal.release_routing import (
        current_version,
        routing_snapshot,
        version_drained,
    )

    kinds = [
        kind
        for kind, marker in (("candidate", "routing.json"), ("retained", "retained.json"))
        if (directory / marker).exists()
        and not (directory / f"{kind}-retired.json").exists()
    ]
    if not kinds:
        return []
    release = installed_release()
    if release is None:
        pending.append("installed_release_unknown")
        return []
    if not await installed_fleet_is_serving(runner, release["digest"]):
        pending.extend(f"{kind}_awaiting_installed_fleet" for kind in kinds)
        return []
    deployment = (
        os.environ.get("TEMPORAL_WORKER_DEPLOYMENT_NAME") or "moonmind-workflow-fleet"
    )
    current = current_version(await routing_snapshot(client, deployment))
    retired = []
    for kind in kinds:
        version = _legacy_cohort_version(directory, kind, deployment)
        # Unknown drainage never releases authority: only Temporal's terminal
        # evidence, or the installed fleet already holding that same route,
        # permits removal.
        if version != current and not await version_drained(client, version):
            pending.append(f"{kind}_awaiting_drainage")
            continue
        await retire_legacy_cohort(directory, owner, kind)
        write_record(
            directory / f"{kind}-retired.json",
            {"owner": owner, "status": "verified_removed", "version": version},
        )
        retired.append(kind)
    return retired


async def reconcile_availability_owner(directory, runner, client):
    """Retire a cohort the deleted availability supervisor left behind.

    That supervisor wrote its own ``release-jobs/<version>/`` directory with a
    ``retained.json`` and no ``request.json``, so the release sweep never
    reaches it. Its ``mm-retained-*`` containers reuse the deployment's Compose
    project and service labels, which keeps ``docker compose ps -q <service>``
    returning more than one id and blocks every later update until they are
    removed.
    """
    retained = json.loads((directory / "retained.json").read_text())
    result = {"job": directory.name, "resumed": False, "retired": [], "pending": []}
    result["retired"].extend(
        await retire_legacy_cohorts(
            directory, runner, client, retained["owner"], result["pending"]
        )
    )
    return result


async def reconcile_release(directory, runner, client):
    """Resume an unfinished release updater and retire its leftovers.

    Recreate-in-place has no routing to reconcile: the installed fleet is the
    only fleet. What remains is durable delivery of the release job itself,
    plus a one-time sweep of cohorts left by earlier blue/green releases.
    """
    request = json.loads((directory / "request.json").read_text())
    owner = request["authored"]["owner"]
    result = {"job": directory.name, "resumed": False, "retired": [], "pending": []}
    updater = f"moonmind-release-update-{directory.name}"
    observed = await inspect_owned(updater, owner)
    if observed and observed["Image"] != request["imageId"]:
        raise ValueError("Release updater image differs from its durable owner")
    # A job authored before recreate-in-place is identified by the blue/green
    # records only that controller wrote. Relaunching it would run the deleted
    # ReleaseCohort algorithm from its own pinned image: it could recreate a
    # candidate or retained cohort beside the installed fleet and promote its
    # older digest over the release that is now installed. Such a job is never
    # resumed; it is retired below and its budget is reported as closed.
    legacy = any(
        (directory / marker).exists() for marker in ("routing.json", "retained.json")
    )
    if legacy and not (directory / "result.json").exists():
        result["pending"].append("legacy_release_not_resumable")
    if not legacy and not (directory / "result.json").exists():
        if observed and observed["State"]["Running"]:
            result["pending"].append("running")
            return result
        delivery_file = directory / "deliveries.json"
        deliveries = (
            json.loads(delivery_file.read_text())["count"]
            if delivery_file.exists()
            else 1
        )
        if deliveries < 3 and time.time() < request["deadline"]:
            write_record(delivery_file, {"count": deliveries + 1})
            if observed is None:
                await launch_updater(runner, directory, request)
            else:
                await docker("start", updater)
            result["resumed"] = True
            return result
        result["pending"].append("execution_budget_exhausted")

    result["retired"].extend(
        await retire_legacy_cohorts(
            directory, runner, client, owner, result["pending"]
        )
    )

    if (
        observed
        and not observed["State"]["Running"]
        and (directory / "result.json").exists()
    ):
        await docker("rm", updater)
    return result




async def reconcile_releases():
    """A bounded maintenance pass; unknown drainage never releases authority."""
    import asyncio
    import fcntl

    from moonmind.config.settings import settings
    from moonmind.utils.logging import redact_sensitive_text
    from moonmind.workflows.temporal.client import get_temporal_client
    from moonmind.workflows.temporal.worker_runtime import (
        _build_deployment_update_executor,
    )

    root = state_root()
    if not root.exists():
        return {"jobs": [], "errors": []}
    executor = _build_deployment_update_executor()
    if executor is None:
        raise ValueError(
            "Deployment reconciliation requires its configured execution substrate"
        )
    runner = replace(
        executor.runner,
        compose_file=executor.runner.compose_file or "/app/release/docker-compose.yaml",
    )
    # Retiring a legacy cohort needs Temporal's drainage and current-route
    # evidence, so this pass still connects. It is dispatched by the
    # Temporal-managed maintenance workflow in any case.
    client = await get_temporal_client(
        settings.temporal.address, settings.temporal.namespace
    )
    result = {"jobs": [], "errors": []}
    # Visit least-recently reconciled jobs first so retained versions cannot
    # starve later jobs. Each pass and each deployment's execution are bounded.
    # Release-owned jobs carry request.json. The deleted availability
    # supervisor owned directories that carry only retained.json, and its
    # mm-retained-* containers block updates until they are removed, so the
    # migration sweep must reach both shapes.
    directories = {path.parent for path in root.glob("*/request.json")}
    directories |= {
        path.parent
        for path in root.glob("*/retained.json")
        if not (path.parent / "request.json").exists()
    }
    requests = sorted(
        directories,
        key=lambda directory: (
            (directory / "maintenance.json").stat().st_mtime
            if (directory / "maintenance.json").exists()
            else 0
        ),
    )
    # The nonblocking acquire yields to a running update; an update waits this
    # pass out instead (see DEPLOYMENT_UPDATE_LOCK_WAIT_SECONDS).
    async with (
        await executor.lock_manager.acquire("moonmind"),
        asyncio.timeout(DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS),
    ):
        for directory in requests[:20]:
            with (directory / "owner.lock").open("a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    outcome = (
                        await reconcile_release(directory, runner, client)
                        if (directory / "request.json").exists()
                        else await reconcile_availability_owner(
                            directory, runner, client
                        )
                    )
                    result["jobs"].append(outcome)
                    write_record(directory / "maintenance.json", outcome)
                except Exception as exc:
                    error = {
                        "job": directory.name,
                        "error": redact_sensitive_text(str(exc))[:500],
                    }
                    result["errors"].append(error)
                    write_record(directory / "maintenance.json", error)
    return result
