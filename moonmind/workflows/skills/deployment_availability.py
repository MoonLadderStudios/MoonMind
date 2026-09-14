"""Route recovery owned by the existing deployment-control process.

This owner uses Docker and Temporal administration directly. It never schedules
its repair onto the application queue whose availability it is repairing.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import time
from dataclasses import replace

import moonmind.utils.logging as utils_logging

from moonmind.workflows.skills.deployment_release import (
    ReleaseCohort,
    docker,
    retained_release_records,
    state_root,
    write_record,
)
from moonmind.workflows.temporal.release_routing import (
    current_version,
    ramping_version,
    routing_snapshot,
    version_availability,
    version_drained,
    verify_ordinary_route,
)


logger = logging.getLogger(__name__)


async def reconcile_availability(client, *, deployment, runner, root):
    """Restore authorized current/ramping or undrained historical cohorts."""
    snapshot = await routing_snapshot(client, deployment)
    current = current_version(snapshot)
    ramping = ramping_version(snapshot)
    versions = {current, ramping} - {"", "__unversioned__"}
    # A successful release is an authority to retain its exact old workers.
    # A never-promoted candidate is not added by receipt discovery.
    errors = []
    for path in root.glob("*/deployment-result.json"):
        source = path.parent / "routing.json"
        if not source.exists():
            continue
        try:
            receipt = json.loads(path.read_text())
            record = json.loads(source.read_text())
            if (
                receipt.get("result", {}).get("status") == "COMPLETED"
                and record["deployment"] == deployment
            ):
                versions.add(f"{deployment}.{record['candidate']}")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if len(errors) < 20:
                errors.append(
                    {"record": path.parent.name, "errorCode": type(exc).__name__}
                )
    for _, retained in retained_release_records(root, deployment, errors=errors):
        versions.add(retained["version"])
    result = {
        "owner": "deployment-control",
        "current": current,
        "versions": [],
        "discoveryErrors": errors,
    }
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
                    if version == current and not retained_file.exists():
                        record["ordinaryTraffic"] = await verify_ordinary_route(
                            client,
                            version=version,
                            canary_id=f"mm-availability-{key}-record-{int(record['observedAt'] * 1000)}",
                        )
                        await cohort.record_serving_image(version, deployment)
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
                                error=utils_logging.redact_sensitive_text(
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
                "error": utils_logging.redact_sensitive_text(
                    str(getattr(exc, "message", None) or exc)
                )[:500],
                "nextAttemptAt": time.time() + 30,
            }
            result["versions"].append(outcome)
            # Keep any existing attempt/deadline evidence when observation fails.
            write_record(directory / "observation-error.json", outcome)
    return result


def _readiness_build_ids(state):
    """Collect distinct buildIds from a /readyz payload including children."""
    if not isinstance(state, dict):
        return set()
    children = state.get("children")
    if isinstance(children, list) and children:
        collected = set()
        for child in children:
            collected.update(_readiness_build_ids(child))
        return collected
    build = state.get("buildId")
    return {str(build)} if build else set()


async def installed_fleet_inventory(runner):
    """Inventory installed worker buildIds across every fleet service.

    Returns a mapping with ``byFleet`` (fleet -> buildId or error detail),
    ``distinctBuildIds`` (sorted list), and ``coherent`` (True only when every
    known fleet reports exactly one live container whose readiness exposes a
    single shared buildId). Any compose/readiness failure makes the inventory
    incoherent rather than raising, so the supervisor can report partial or
    conflicting identities instead of trusting this process's own build.
    """
    from moonmind.workflows.skills.deployment_release import worker_readiness
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

    by_fleet = {}
    distinct = set()
    for fleet, service in _FLEET_SERVICE_NAMES.items():
        try:
            found = await runner._run_compose_command(
                ("docker", "compose", "ps", "-q", service)
            )
        except Exception as exc:
            by_fleet[fleet] = {"status": "inventory_error"}
            continue
        if not isinstance(found, dict) or found.get("exitCode", 1) != 0:
            by_fleet[fleet] = {"status": "inventory_error"}
            continue
        identifiers = str(found.get("stdout") or "").split()
        if len(identifiers) != 1:
            by_fleet[fleet] = {
                "status": "partial",
                "containers": len(identifiers),
            }
            continue
        try:
            state = await worker_readiness(identifiers[0])
        except Exception:
            by_fleet[fleet] = {"status": "unready"}
            continue
        if not isinstance(state, dict) or state.get("ready") is not True:
            by_fleet[fleet] = {"status": "unready"}
            continue
        build_ids = _readiness_build_ids(state)
        if len(build_ids) != 1:
            by_fleet[fleet] = {"status": "conflicting"}
            distinct.update(build_ids)
            continue
        build_id = next(iter(build_ids))
        by_fleet[fleet] = {"status": "ready", "buildId": build_id}
        distinct.add(build_id)
    coherent = bool(by_fleet) and len(distinct) == 1 and all(
        entry.get("status") == "ready" for entry in by_fleet.values()
    )
    return {
        "byFleet": by_fleet,
        "distinctBuildIds": sorted(distinct),
        "coherent": coherent,
    }


async def supervise_availability(client, spec, metadata, *, stop=None):
    """Startup and periodic owner; cancellation follows the worker lifecycle."""
    from moonmind.workflows.skills.deployment_maintenance import reconcile_releases
    from moonmind.workflows.temporal.worker_runtime import (
        _build_deployment_update_executor,
    )

    stop = stop or asyncio.Event()
    last_routing = None
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
                inventory = await installed_fleet_inventory(runner)
                availability = await reconcile_availability(
                    client, deployment=spec.deployment_id, runner=runner, root=root
                )
                observed = availability["current"]
                spec_candidate = f"{spec.deployment_id}.{spec.build_id}"
                distinct = inventory["distinctBuildIds"]
                has_inventory = bool(distinct)
                if inventory["coherent"]:
                    candidate = f"{spec.deployment_id}.{distinct[0]}"
                    installed_versions = [candidate]
                else:
                    candidate = spec_candidate
                    installed_versions = [
                        f"{spec.deployment_id}.{build_id}" for build_id in distinct
                    ]
                drift_evidence = has_inventory and not inventory["coherent"]
                routing = {
                    "status": (
                        "current"
                        if observed == candidate and not drift_evidence
                        else "awaiting_promotion"
                    ),
                    "currentVersion": observed,
                    "candidateVersion": candidate,
                    "recoveryOwner": "deployment-control",
                }
                if drift_evidence:
                    routing.update(
                        installedVersions=installed_versions,
                        installedByFleet=inventory["byFleet"],
                        installedCoherent=False,
                    )
                if observed != candidate or drift_evidence:
                    if drift_evidence:
                        routing.update(
                            recoverySkill="update-moonmind",
                            message=(
                                "Installed worker fleets report partial or "
                                "conflicting releases. "
                                f"Observed fleets: {', '.join(installed_versions)}. "
                                "Verify every installed fleet before declaring "
                                "routing current. Resume the authorized release "
                                "submission or run "
                                "bash tools/update-moonmind.sh --branch <selected-branch> "
                                "to qualify and promote the intended release. "
                                "Docker Compose replacement alone does not promote routing."
                            ),
                        )
                    else:
                        routing.update(
                            recoverySkill="update-moonmind",
                            message=(
                                "The installed release is not the current Temporal route. "
                                "Ordinary workflows still use that release's behavior, "
                                "even when retained workers are available. "
                                "Resume the authorized release submission or run "
                                "bash tools/update-moonmind.sh --branch <selected-branch> "
                                "to qualify and promote the intended release. "
                                "Docker Compose replacement alone does not promote routing."
                            ),
                        )
                    routing_key = (
                        observed,
                        candidate,
                        ",".join(installed_versions),
                        str(inventory["coherent"]),
                    )
                    if last_routing != routing_key:
                        logger.warning(
                            "%s Current version: %s; installed version: %s",
                            routing["message"], observed, candidate,
                        )
                last_routing = (
                    observed,
                    candidate,
                    ",".join(installed_versions),
                    str(inventory["coherent"]),
                )
                metadata["releaseRouting"] = routing
                # Retained pollers prove availability of the current route, not
                # activation of the installed fix. Preserve both facts durably.
                # Publish versions and routing atomically so observers never see
                # versions without their matching routing decision.
                availability["routing"] = routing
                metadata["releaseAvailability"] = availability
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
                "error": utils_logging.redact_sensitive_text(
                    str(getattr(exc, "message", None) or exc)
                )[:500],
                "nextAttemptAt": time.time() + 30,
            }
            try:
                write_record(
                    state_root() / "availability.json", metadata["releaseAvailability"]
                )
            except (OSError, ValueError):
                # Readiness metadata still exposes the failure when durable
                # projection storage is unavailable; retry on the next sweep.
                pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            # The periodic timeout starts another bounded observation sweep.
            pass
