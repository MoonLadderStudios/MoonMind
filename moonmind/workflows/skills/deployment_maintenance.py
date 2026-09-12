"""Reconcile release ownership using the existing deployment maintenance fleet."""

from __future__ import annotations

import json
import time
from dataclasses import replace

from moonmind.workflows.skills.deployment_release import (
    ReleaseCohort,
    docker,
    inspect_owned,
    state_root,
    write_record,
)


async def reconcile_release(directory, runner, client):
    from moonmind.workflows.temporal.release_routing import (
        current_version,
        qualification_closed_without_activation,
        routing_snapshot,
        version_drained,
    )
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

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
        if observed and deliveries < 3 and time.time() < request["deadline"]:
            write_record(delivery_file, {"count": deliveries + 1})
            await docker("start", updater)
            result["resumed"] = True
            return result
        result["pending"].append(
            "execution_budget_exhausted" if observed else "owner_missing"
        )

    routing_file = directory / "routing.json"
    if not routing_file.exists():
        return result
    routing = json.loads(routing_file.read_text())
    deployment = routing["deployment"]
    candidate = f"{deployment}.{routing['candidate']}"
    current = current_version(await routing_snapshot(client, deployment))
    cohort = ReleaseCohort(runner, directory, owner)
    # Active routing can release temporary candidate pollers only after the
    # installed fleet has objectively assumed that exact release. Older routing
    # can release them only when Temporal certifies it has drained.
    candidate_safe = (
        await version_drained(client, candidate) if current != candidate else False
    )
    primary_file = directory / "deployment-result.json"
    if current == candidate and primary_file.exists():
        primary = json.loads(primary_file.read_text())
        if primary["owner"] != owner:
            raise ValueError("Deployment receipt owner differs")
        if primary["result"]["status"] == "COMPLETED":
            await cohort.verify_installed(
                request["image"], expected=routing["candidate"], attempts=1
            )
            candidate_safe = True
    unused = False
    if (
        not candidate_safe
        and current != candidate
        and (directory / "result.json").exists()
    ):
        unused = await qualification_closed_without_activation(
            client, version=candidate, canary_id=f"mm-release-canary-{directory.name}"
        )
        candidate_safe = unused
    groups = [("candidate", candidate_safe)]
    retained_file = directory / "retained.json"
    if retained_file.exists():
        retained = json.loads(retained_file.read_text())
        if retained["owner"] != owner:
            raise ValueError("Retained release owner differs")
        retained_safe = await version_drained(client, retained["version"])
        if not retained_safe and unused and current == retained["version"]:
            await cohort.verify_installed(
                retained["image"],
                expected=current.removeprefix(deployment + "."),
                attempts=1,
            )
            retained_safe = True
        groups.append(("retained", retained_safe))
    for kind, safe in groups:
        receipt = directory / f"{kind}-retired.json"
        if receipt.exists():
            continue
        if not safe:
            result["pending"].append(kind)
            continue
        cohort.names = [
            f"mm-{kind}-{directory.name[:16]}-{fleet.replace('_', '-')}"
            for fleet in _FLEET_SERVICE_NAMES
        ]
        if kind == "candidate":
            cohort.names.append(f"mm-candidate-{directory.name[:16]}-api")
        await cohort.cleanup()
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
    async with await executor.lock_manager.acquire("moonmind"), asyncio.timeout(840):
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
