"""Route recovery owned by the existing deployment-control process.

This owner uses Docker and Temporal administration directly. It never schedules
its repair onto the application queue whose availability it is repairing.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import time
from dataclasses import replace
from moonmind.utils.logging import redact_sensitive_text

from moonmind.workflows.skills.deployment_release import (
    ReleaseCohort,
    docker,
    state_root,
    write_record,
)
from moonmind.workflows.temporal.release_routing import (
    current_version,
    routing_snapshot,
    version_availability,
    version_drained,
    verify_ordinary_route,
)


async def reconcile_availability(client, *, deployment, runner, root):
    """Restore authorized current/ramping or undrained historical cohorts."""
    snapshot = await routing_snapshot(client, deployment)
    current = current_version(snapshot)
    routing = snapshot.worker_deployment_info.routing_config
    ramping = routing.ramping_version
    versions = {current, ramping} - {"", "__unversioned__"}
    # A successful release is an authority to retain its exact old workers.
    # A never-promoted candidate is not added by receipt discovery.
    for path in root.glob("*/deployment-result.json"):
        source = path.parent / "routing.json"
        if source.exists():
            receipt = json.loads(path.read_text())
            record = json.loads(source.read_text())
            if (
                receipt.get("result", {}).get("status") == "COMPLETED"
                and record["deployment"] == deployment
            ):
                versions.add(f"{deployment}.{record['candidate']}")
    result = {"owner": "deployment-control", "current": current, "versions": []}
    for version in sorted(
        versions, key=lambda value: (value != current, value != ramping, value)
    ):
        key = hashlib.sha256(version.encode()).hexdigest()[:32]
        directory = root / key
        directory.mkdir(parents=True, exist_ok=True)
        owner = f"release-availability:{version}"
        cohort = ReleaseCohort(runner, directory, owner)
        try:
            with (directory / "owner.lock").open("a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    result["versions"].append({"version": version, "phase": "owned"})
                    continue
                record_file = directory / "availability.json"
                prior = (
                    json.loads(record_file.read_text()) if record_file.exists() else {}
                )
                if (
                    version not in {current, ramping}
                    and prior.get("phase") == "drained"
                ):
                    continue
                if version not in {current, ramping} and await version_drained(
                    client, version
                ):
                    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

                    cohort.names = [
                        f"mm-retained-{directory.name[:16]}-{fleet.replace('_', '-')}"
                        for fleet in _FLEET_SERVICE_NAMES
                    ]
                    await cohort.cleanup()
                    write_record(
                        directory / "availability.json",
                        {"owner": owner, "phase": "drained"},
                    )
                    continue
                live = set(
                    (await docker("ps", "--no-trunc", "--format", "{{.ID}}")).split()
                )
                observation = await version_availability(
                    client, version, live_container_ids=live
                )
                record = {
                    **prior,
                    **observation,
                    "owner": owner,
                    "observedAt": time.time(),
                }
                if observation["available"]:
                    record.update(phase="available", attempts=0, deadline=None)
                    retained_file = directory / "retained.json"
                    if (
                        version == current
                        and retained_file.exists()
                        and record.get("servingCohort") != "installed"
                    ):
                        retained = json.loads(retained_file.read_text())
                        try:
                            await cohort.verify_installed(
                                retained["image"],
                                expected=version.removeprefix(deployment + "."),
                                attempts=1,
                            )
                        except RuntimeError:
                            record["servingCohort"] = "retained"
                        else:
                            record["ordinaryTraffic"] = await verify_ordinary_route(
                                client,
                                version=version,
                                canary_id=f"mm-availability-{key}-installed-{int(record['observedAt'] * 1000)}",
                            )
                            from moonmind.workflows.temporal.workers import (
                                _FLEET_SERVICE_NAMES,
                            )

                            cohort.names = [
                                f"mm-retained-{directory.name[:16]}-{fleet.replace('_', '-')}"
                                for fleet in _FLEET_SERVICE_NAMES
                            ]
                            await cohort.cleanup()
                            record["servingCohort"] = "installed"
                else:
                    deadline = prior.get("deadline") or time.time() + 300
                    attempts = prior.get("attempts", 0)
                    record.update(
                        deadline=deadline, phase="recovering", servingCohort="retained"
                    )
                    if attempts >= 5 or time.time() >= deadline:
                        record["phase"] = "exhausted"
                    else:
                        record["attempts"] = attempts + 1
                        write_record(record_file, record)
                        try:
                            async with asyncio.timeout(max(1, deadline - time.time())):
                                # Restore the existing route. Only the release job
                                # can qualify and CAS-promote its authored candidate.
                                await cohort.preserve_previous(version, deployment, "")
                                record.update(
                                    await version_availability(client, version)
                                )
                                if version == current:
                                    record["ordinaryTraffic"] = (
                                        await verify_ordinary_route(
                                            client,
                                            version=version,
                                            canary_id=f"mm-availability-{key}-{int(deadline * 1000)}-{record['attempts']}",
                                            timeout_seconds=max(
                                                1, int(deadline - time.time())
                                            ),
                                        )
                                    )
                                record["phase"] = (
                                    "available" if record["available"] else "recovering"
                                )
                        except Exception as exc:
                            record.update(
                                phase="retrying",
                                errorCode=type(exc).__name__,
                                error=redact_sensitive_text(
                                    str(getattr(exc, "message", None) or exc)
                                )[:500],
                            )
                record["nextAttemptAt"] = (
                    time.time() + 30 if record["phase"] == "retrying" else None
                )
                write_record(record_file, record)
                result["versions"].append(record)
        except Exception as exc:
            outcome = {
                "owner": owner,
                "version": version,
                "phase": "retrying",
                "errorCode": type(exc).__name__,
                "error": redact_sensitive_text(
                    str(getattr(exc, "message", None) or exc)
                )[:500],
                "nextAttemptAt": time.time() + 30,
            }
            result["versions"].append(outcome)
            # Keep any existing attempt/deadline evidence when observation fails.
            write_record(directory / "observation-error.json", outcome)
    return result


async def supervise_availability(client, spec, metadata, *, stop=None):
    """Startup and periodic owner; cancellation follows the worker lifecycle."""
    from moonmind.workflows.skills.deployment_maintenance import reconcile_releases
    from moonmind.workflows.temporal.worker_runtime import (
        _build_deployment_update_executor,
    )

    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            executor = _build_deployment_update_executor()
            if executor is None:
                raise ValueError("Deployment recovery substrate is unavailable")
            runner = replace(
                executor.runner,
                compose_file=executor.runner.compose_file
                or "/app/release/docker-compose.yaml",
            )
            root = state_root()
            root.mkdir(parents=True, exist_ok=True)
            async with (
                await executor.lock_manager.acquire("moonmind"),
                asyncio.timeout(300),
            ):
                metadata["releaseAvailability"] = await reconcile_availability(
                    client, deployment=spec.deployment_id, runner=runner, root=root
                )
                observed = metadata["releaseAvailability"]["current"]
                metadata["releaseRouting"] = {
                    "status": (
                        "current"
                        if observed == f"{spec.deployment_id}.{spec.build_id}"
                        else "awaiting_promotion"
                    ),
                    "currentVersion": observed,
                    "candidateVersion": f"{spec.deployment_id}.{spec.build_id}",
                    "recoveryOwner": "deployment-control",
                }
                write_record(
                    root / "availability.json", metadata["releaseAvailability"]
                )
            # The same durable job owner resumes updates and retires cohorts.
            # This call is reachable even if no Activity can currently dispatch.
            try:
                async with asyncio.timeout(20):
                    metadata["releaseMaintenance"] = await reconcile_releases()
            except Exception as exc:
                metadata["releaseMaintenance"] = {
                    "phase": "retrying",
                    "errorCode": type(exc).__name__,
                }
        except Exception as exc:
            metadata["releaseAvailability"] = {
                "owner": "deployment-control",
                "phase": "retrying",
                "errorCode": type(exc).__name__,
                "error": redact_sensitive_text(
                    str(getattr(exc, "message", None) or exc)
                )[:500],
                "nextAttemptAt": time.time() + 30,
            }
            try:
                write_record(
                    state_root() / "availability.json", metadata["releaseAvailability"]
                )
            except (OSError, ValueError):
                pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass
