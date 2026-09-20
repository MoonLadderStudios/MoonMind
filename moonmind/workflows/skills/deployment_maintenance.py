"""Reconcile release ownership using the existing deployment maintenance fleet."""

from __future__ import annotations

import json
import time
from dataclasses import replace

from moonmind.workflows.skills.deployment_execution import (
    DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS,
)
from moonmind.workflows.skills.deployment_release import (
    docker,
    inspect_owned,
    launch_updater,
    state_root,
    write_record,
)


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
    if not (directory / "result.json").exists():
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

    for kind, marker in (("candidate", "routing.json"), ("retained", "retained.json")):
        if not (directory / marker).exists():
            continue
        receipt = directory / f"{kind}-retired.json"
        if receipt.exists():
            continue
        await retire_legacy_cohort(directory, owner, kind)
        write_record(receipt, {"owner": owner, "status": "verified_removed"})
        result["retired"].append(kind)

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
    client = await get_temporal_client(
        settings.temporal.address, settings.temporal.namespace
    )
    result = {"jobs": [], "errors": []}
    # Visit least-recently reconciled jobs first so retained versions cannot
    # starve later jobs. Each pass and each deployment's execution are bounded.
    requests = sorted(
        root.glob("*/request.json"),
        key=lambda path: (
            (path.parent / "maintenance.json").stat().st_mtime
            if (path.parent / "maintenance.json").exists()
            else 0
        ),
    )
    # The nonblocking acquire yields to a running update; an update waits this
    # pass out instead (see DEPLOYMENT_UPDATE_LOCK_WAIT_SECONDS).
    async with (
        await executor.lock_manager.acquire("moonmind"),
        asyncio.timeout(DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS),
    ):
        for request_file in requests[:20]:
            directory = request_file.parent
            with (directory / "owner.lock").open("a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    outcome = await reconcile_release(directory, runner, client)
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
