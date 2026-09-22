"""Durable on-demand ownership for a release that replaces its caller.

The existing deployment worker supplies its Compose/credential/network boundary.
One named updater survives Activity replacement; its input and terminal receipt
live in the deployment-owned state volume. No additional idle service is added.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from moonmind.workflows.skills.deployment_execution import (
    ToolFailure,
    ToolResult,
    _atomic_write_bytes,
    _ensure_command_succeeded,
    _parse_inputs,
    _requested_image,
    _resolved_digest_from_target_image,
)
from moonmind.workflows.skills.deployment_tools import RELEASE_JOB_BUDGET_SECONDS

CONTROL_SERVICE = "temporal-worker-deployment-control"

DIAGNOSIS_BOUND = 1000
_DIAGNOSIS_ELISION = "\n...[elided]...\n"


def bounded_diagnosis(text, limit=DIAGNOSIS_BOUND):
    """Bound a recorded failure without discarding the line that names it.

    A release failure identifies itself at both ends: the opening names the
    fleet and the observed gateway health, while the retained worker's log
    tail ends in the exception Python raised. Keeping only the head published
    frame stacks and dropped the ``RuntimeError: ...`` line, leaving every
    operator surface fed from this record unable to say why the release
    failed. Redaction runs over the whole text before this bound, so neither
    retained end can publish a value the redaction removed.
    """
    if len(text) <= limit:
        return text
    keep = limit - len(_DIAGNOSIS_ELISION)
    head = keep // 2
    return text[:head] + _DIAGNOSIS_ELISION + text[len(text) - (keep - head) :]


def record_attempt_error(directory, owner, attempt, error):
    """Keep every attempt's error, so later noise cannot erase the first one.

    An attempt that did real work and failed for its own reason is followed by
    attempts that can fail for unrelated, transient reasons - losing a race for
    the stack lock takes seconds. Overwriting the record left
    ``last-error.json``, the terminal receipt, the Temporal failure and the
    operator's incident reconstruction naming only that last reason, with the
    cause unrecoverable. The latest error stays at the top level, so existing
    readers of this record are unchanged.
    """
    path = directory / "last-error.json"
    history = []
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        recorded = previous.get("attempts")
        if isinstance(recorded, list) and recorded:
            history = list(recorded)
        elif previous.get("error"):
            # A release already running when this history was introduced has a
            # record carrying only the top-level attempt and error. Seed the
            # history from it so the update that adds the history does not
            # erase the failure the history exists to preserve.
            history = [
                {
                    "attempt": previous.get("attempt") or 1,
                    "error": previous["error"],
                }
            ]
    history.append({"attempt": attempt, "error": error})
    write_record(
        path,
        {"owner": owner, "attempt": attempt, "error": error, "attempts": history},
    )
    return history


def release_failure_summary(history):
    """Name the failure that started the release, not only the last one."""
    if not history:
        return "Release update exhausted its durable retry budget"
    first, last = history[0], history[-1]
    if first["error"] == last["error"]:
        return first["error"]
    return bounded_diagnosis(
        f"attempt {first['attempt']}: {first['error']}"
        f" (final attempt {last['attempt']}: {last['error']})"
    )


# Polls one gateway recreate keeps to itself before another may replace it.
# The gateway healthcheck runs every 10s after a 10s start period and needs
# three passes, so a recreate that is going to work reports healthy well
# inside this cooldown.
_GATEWAY_REPAIR_COOLDOWN_POLLS = 20


async def docker(*args, input_bytes=None):
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), 360)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        from moonmind.utils.logging import redact_sensitive_text

        diagnostic = stderr.decode(errors="replace").strip()
        reason = redact_sensitive_text(diagnostic or "no diagnostic")
        raise RuntimeError(f"Docker {args[0]} failed: {reason[:1000]}")
    return stdout.decode().strip()


async def inspect_owned(name, owner):
    # Listing is an explicit existence check. An unreadable daemon never means
    # the previous launch did not happen.
    identifiers = await docker("ps", "-aq", "--filter", f"name=^/{name}$")
    if not identifiers:
        return None
    rows = json.loads(await docker("inspect", identifiers))
    if (
        len(rows) != 1
        or rows[0]["Config"].get("Labels", {}).get("moonmind.release.owner") != owner
    ):
        raise ValueError("Release container ownership differs")
    return rows[0]


def _activity_schedule_anchor():
    """Wall clock instant the supervising Activity was scheduled.

    Temporal starts its schedule-to-close clock when the Activity is
    scheduled, not when it begins running, and the deployment fleet runs one
    Activity at a time. A release that waited behind another one would
    otherwise start its own budget late enough to outlive the supervisor that
    is meant to observe it. ``None`` outside an Activity - the host-update
    entrypoint and tests - where the caller falls back to the current time.
    """
    try:
        from temporalio import activity

        scheduled = activity.info().scheduled_time
    except (ImportError, RuntimeError):
        return None
    return scheduled.timestamp() if scheduled is not None else None


def write_record(path, value):
    _atomic_write_bytes(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def reserve_record(path, value):
    """Publish complete bytes without replacing another delivery's decision."""
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # Another delivery published first; read its authoritative bytes.
            pass
        return json.loads(path.read_text())
    finally:
        Path(temporary).unlink(missing_ok=True)


async def require_coherent_images(runner, image):
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

    result = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"),
        requested_image=image,
        max_stdout_chars=None,
    )
    _ensure_command_succeeded("render release", result)
    services = json.loads(result["stdout"])["services"]
    for service in _FLEET_SERVICE_NAMES.values():
        configured = services.get(service, {})
        if configured.get("image") != image:
            raise ValueError(
                f"Release requires {service} to use the selected immutable image"
            )
        if str(
            configured.get("environment", {}).get(
                "TEMPORAL_WORKER_VERSIONING_ENABLED", "auto"
            )
        ).lower() not in {"auto", "true", "1"}:
            raise ValueError(f"Release requires versioned routing on {service}")
    return services


async def worker_readiness(container):
    return json.loads(
        await docker(
            "exec",
            container,
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/readyz', timeout=5).read().decode())",
        )
    )


async def container_health(name):
    """Observed health of one container; ``None`` when it reports none."""
    try:
        observed = json.loads(await docker("inspect", name))[0]
        return (observed["State"].get("Health") or {}).get("Status")
    except (RuntimeError, ValueError, LookupError, TypeError):
        # A missing, foreign or unreadable container reports no health rather
        # than replacing the caller's failure with an inspection error.
        return None


def readiness_matches(state, digest):
    if state.get("ready") is not True:
        return False
    # A worker can be ready while routing still targets the outgoing version:
    # startup parks rather than displacing a live route, and only a background
    # task retries the promotion. Treating that as verified issued a success
    # receipt while ordinary work still routed elsewhere, with no proof the
    # handoff ever completed. Verification fails closed until routing moves.
    routing = state.get("releaseRouting")
    if isinstance(routing, dict) and routing.get("status") == "awaiting_promotion":
        return False
    if "children" in state:
        children = state["children"]
        return bool(children) and all(
            readiness_matches(child, digest) for child in children
        )
    return state.get("buildId") == digest


def state_root():
    path = os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE")
    if not path:
        raise ValueError(
            "Release update requires the deployment-owned durable state volume"
        )
    return Path(path).parent / "release-jobs"


async def launch_updater(runner, directory, request):
    """One immutable launch path shared by submission and owner recovery."""
    owner = request["authored"]["owner"]
    name = f"moonmind-release-update-{directory.name}"
    observed = await inspect_owned(name, owner)
    if observed is None:
        launched = await runner._run_compose_command(
            (
                "docker",
                "compose",
                "run",
                "-d",
                "--no-deps",
                "--name",
                name,
                "--label",
                f"moonmind.release.owner={owner}",
                "--entrypoint",
                "python",
                CONTROL_SERVICE,
                "-m",
                "moonmind.workflows.skills.deployment_release",
                str(directory / "request.json"),
            ),
            requested_image=request["image"],
        )
        observed = await inspect_owned(name, owner)
        if observed is None:
            _ensure_command_succeeded("launch updater", launched)
            raise RuntimeError("Updater launch has no remotely verified owner")
    if observed["Image"] != request["imageId"]:
        raise ValueError("Updater container image differs from the pinned release")
    await docker("update", "--restart=no", name)
    return observed


def retained_release_records(root, deployment, *, errors=None):
    """Yield image authority recorded by a release or its availability owner."""
    for path in sorted(root.glob("*/retained.json")):
        directory = path.parent
        request_file = directory / "request.json"
        routing_file = directory / "routing.json"
        try:
            retained = json.loads(path.read_text())
            if (
                not isinstance(retained.get("owner"), str)
                or not retained["owner"]
                or not isinstance(retained.get("version"), str)
                or not retained["version"].startswith(deployment + ".")
                or not isinstance(retained.get("image"), str)
                or not retained["image"].startswith("sha256:")
            ):
                raise ValueError("Retained release receipt authority differs")
            # The availability owner records the exact serving image before
            # loss, including plain Compose installations with no update job.
            # Its deterministic directory and owner bind that durable receipt;
            # mutable observation/retry records are not image authority.
            availability_owner = f"release-availability:{retained['version']}"
            availability_key = hashlib.sha256(
                retained["version"].encode()
            ).hexdigest()[:32]
            if retained["owner"] == availability_owner:
                if directory.name != availability_key:
                    raise ValueError("Retained availability receipt owner differs")
            else:
                if not request_file.exists() or not routing_file.exists():
                    continue
                routing = json.loads(routing_file.read_text())
                if routing.get("deployment") != deployment:
                    continue
                request = json.loads(request_file.read_text())
                if (
                    retained["owner"] != request["authored"]["owner"]
                    or retained["version"] != routing.get("previous")
                ):
                    raise ValueError("Retained release receipt authority differs")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if errors is not None and len(errors) < 20:
                errors.append(
                    {"record": directory.name, "errorCode": type(exc).__name__}
                )
            # Invalid evidence grants no image authority. Its failure must not
            # suppress valid current or retained cohorts from another job.
            continue
        yield directory, retained


async def successful_release_image(root, version):
    """Recover image authority from an exact successful immutable release.

    A local image, a newer request, or a failed promotion grants no authority.
    Validate the image's own manifest before reconstructing any worker cohort.
    """
    for path in sorted(root.glob("*/routing.json")):
        try:
            routing = json.loads(path.read_text())
            if f"{routing.get('deployment')}.{routing.get('candidate')}" != version:
                continue
        except (OSError, ValueError, TypeError, AttributeError):
            # Malformed unrelated jobs cannot revoke a valid release receipt.
            continue
        directory = path.parent
        try:
            receipt_file = directory / "deployment-result.json"
            request_file = directory / "request.json"
            if not receipt_file.exists() or not request_file.exists():
                continue
            receipt = json.loads(receipt_file.read_text())
            request = json.loads(request_file.read_text())
            if receipt.get("owner") != request["authored"]["owner"]:
                raise ValueError("Successful release receipt owner differs")
            if receipt.get("result", {}).get("status") != "COMPLETED":
                continue
            digest = request["image"].partition("@")[2] or request["image"]
            outputs = receipt["result"].get("outputs", {})
            if not digest.startswith("sha256:") or outputs.get("resolvedDigest") != digest:
                raise ValueError("Successful release receipt image differs")
            expected_source = request["authored"]["inputs"].get("sourceRevision")
            if "sourceRevision" in outputs and outputs.get("sourceRevision") != expected_source:
                raise ValueError("Successful release receipt source differs")
            # Receipts predating the bound source stamp carry no sourceRevision
            # output; their source authority still binds through the live image
            # manifest check below, so absence alone is not rejection evidence.
            image_ids = set((await docker("image", "ls", "-q", "--no-trunc")).split())
            if request["imageId"] not in image_ids:
                await docker("pull", request["image"])
            image = json.loads(await docker("image", "inspect", request["imageId"]))[0]
            if image["Id"] != request["imageId"]:
                raise ValueError("Successful release image identity differs")
            manifest = json.loads(
                await docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    request["imageId"],
                    "-c",
                    "import json; from moonmind.release_identity import installed_release; "
                    "print(json.dumps(installed_release()))",
                )
            )
            if (
                not manifest
                or manifest.get("digest") != routing["candidate"]
                or (
                    manifest.get("sourceRevision")
                    != request["authored"]["inputs"].get("sourceRevision")
                )
            ):
                raise ValueError(
                    "Successful release manifest differs from its source authority"
                )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Invalid evidence grants no image authority. Its failure must not
            # suppress valid current or retained cohorts from another job.
            # In particular, receipts authored before sourceRevision existed
            # cannot prove source identity and are skipped, not fatal.
            continue
        else:
            return {
                "image": request["imageId"],
                "sourceReceipt": directory.name,
                "sourceRevision": manifest["sourceRevision"],
            }
    # An initial Compose installation may never have produced its own release
    # receipt. Its availability owner or first updater records the proven image.
    deployment, _, expected_digest = version.partition(".sha256:")
    if expected_digest:
        for directory, retained in retained_release_records(root, deployment):
            if retained["version"] != version:
                continue
            image_id = retained["image"]
            if not image_id.startswith("sha256:"):
                raise ValueError("Retained release image is not content addressed")
            image = json.loads(await docker("image", "inspect", image_id))[0]
            if image["Id"] != image_id:
                raise ValueError("Retained release image identity differs")
            manifest = json.loads(
                await docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    image_id,
                    "-c",
                    "import json; from moonmind.release_identity import installed_release; "
                    "print(json.dumps(installed_release()))",
                )
            )
            if not manifest or manifest.get("digest") != "sha256:" + expected_digest:
                raise ValueError("Retained release manifest differs from its version")
            return {
                "image": image_id,
                "sourceReceipt": directory.name,
                "sourceRevision": manifest["sourceRevision"],
            }
    return None


async def docker_logs_tail(name, tail_lines=30, timeout_seconds=60):
    """Return the merged stdout+stderr tail for a release container.

    Updater tracebacks (for example the ``FileNotFoundError`` from an empty
    state volume) are written to stderr, while the shared ``docker()`` helper
    returns only stdout. Merging both streams keeps the diagnosis from
    reporting ``(empty)`` for exactly the failures it must surface.
    """
    process = await asyncio.create_subprocess_exec(
        "docker",
        "logs",
        "--tail",
        str(tail_lines),
        name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"Docker logs failed for {name}")
    merged = stdout.decode(errors="replace")
    if stderr:
        merged += ("\n" if merged and not merged.endswith("\n") else "") + stderr.decode(
            errors="replace"
        )
    return merged.strip()


async def execute_detached(executor, inputs, context):
    """Launch once or reattach, without retaining self-recreation authority."""
    owner = str(context.get("idempotency_key") or context.get("workflow_id") or "")
    if not owner:
        raise ValueError("Release update requires a durable execution identity")
    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    directory = state_root() / key
    directory.mkdir(parents=True, exist_ok=True)
    request_file, result_file = directory / "request.json", directory / "result.json"
    # Anchor the durable deadline where the supervising Activity's own clock
    # starts, before any pre-launch work. Pulling and inspecting the updater
    # image is allowed a full runner command timeout, and the Activity may
    # have queued behind another deployment before that, so a deadline taken
    # later would start after the schedule it must stay inside. The anchor is
    # reserved, not written, so a re-attaching attempt inherits the first
    # delivery's deadline instead of extending it.
    anchor = _activity_schedule_anchor()
    deadline = reserve_record(
        directory / "deadline.json",
        {
            "owner": owner,
            "deadline": (time.time() if anchor is None else anchor)
            + RELEASE_JOB_BUDGET_SECONDS,
        },
    )["deadline"]
    name = f"moonmind-release-update-{key}"
    safe_context = {
        key: context[key]
        for key in (
            "idempotency_key",
            "workflow_id",
            "run_id",
            "source_run_id",
            "task_id",
            "operator",
            "operator_role",
            "principal",
            "principal_role",
            "execution_ref",
            "deployment_evidence_principal",
            "deployment_operator_urls",
        )
        if key in context
    }
    authored = {"inputs": dict(inputs), "context": safe_context, "owner": owner}
    if request_file.exists():
        record = json.loads(request_file.read_text())
        if record["authored"] != authored:
            raise ValueError("Release identity is already bound to different inputs")
    else:
        parsed = _parse_inputs(inputs)
        requested = _requested_image(parsed)
        pulled = await executor.runner.pull(
            stack=parsed["stack"],
            command=("docker", "compose", "pull", CONTROL_SERVICE),
            requested_image=requested,
        )
        _ensure_command_succeeded("pull updater", pulled)
        image = await executor.runner.inspect_image(requested)
        source_revision = inputs.get("sourceRevision")
        if (
            source_revision
            and image.get("Config", {})
            .get("Labels", {})
            .get("org.opencontainers.image.revision")
            != source_revision
        ):
            raise ValueError(
                "Updater image source revision differs from the selected branch snapshot"
            )
        digest = _resolved_digest_from_target_image(
            repository=parsed["image"]["repository"], target_image=image
        )
        if not digest or (parsed["image"].get("resolvedDigest") not in (None, digest)):
            raise ValueError("Updater image identity is unverified")
        record = {
            "authored": authored,
            "image": f"{parsed['image']['repository']}@{digest}",
            "imageId": image["Id"],
            # Anchored above, before the pull, so the job and the Activity
            # supervising it cannot disagree about when the release is still
            # allowed to be running.
            "deadline": deadline,
        }
        record = reserve_record(request_file, record)
        if record["authored"] != authored:
            raise ValueError("Release identity is already bound to different inputs")
    if not result_file.exists():
        await require_coherent_images(executor.runner, record["image"])
        await launch_updater(executor.runner, directory, record)
        while not result_file.exists():
            if time.time() >= record["deadline"]:
                raise RuntimeError(
                    "Release update exhausted its durable"
                    f" {RELEASE_JOB_BUDGET_SECONDS // 3600}-hour budget"
                )
            existing = await inspect_owned(name, owner)
            if existing is None:
                raise RuntimeError(
                    "Release updater owner disappeared; retained job requires resumption"
                )
            if not existing["State"]["Running"]:
                attempts_file = directory / "deliveries.json"
                deliveries = (
                    json.loads(attempts_file.read_text())["count"]
                    if attempts_file.exists()
                    else 1
                )
                if deliveries >= 3:
                    from moonmind.utils.logging import redact_sensitive_text

                    diagnosis = []
                    try:
                        inner_attempts = directory / "attempts.json"
                        diagnosis.append(
                            "attempts="
                            + (
                                str(
                                    json.loads(inner_attempts.read_text()).get(
                                        "count"
                                    )
                                )
                                if inner_attempts.exists()
                                else "none (updater never entered its retry loop)"
                            )
                        )
                    except (OSError, ValueError):
                        diagnosis.append("attempts=unreadable")
                    try:
                        last_error_file = directory / "last-error.json"
                        if last_error_file.exists():
                            last_error = json.loads(last_error_file.read_text())
                            history = list(last_error.get("attempts") or [])
                            if history and history[0].get("error") != last_error.get(
                                "error"
                            ):
                                diagnosis.append(
                                    "first-error="
                                    + redact_sensitive_text(
                                        str(history[0].get("error"))
                                    )[:500]
                                )
                            diagnosis.append(
                                "last-error="
                                + redact_sensitive_text(
                                    str(last_error.get("error") or last_error)
                                )[:500]
                            )
                    except (OSError, ValueError):
                        diagnosis.append("last-error=unreadable")
                    try:
                        state = existing.get("State", {})
                        diagnosis.append(f"updater-exit={state.get('ExitCode')}")
                    except (AttributeError, TypeError):
                        # inspect_owned contracts a Mapping; a foreign shape
                        # carries no exit evidence, so record that explicitly.
                        diagnosis.append("updater-exit=unknown")
                    try:
                        tail = await docker_logs_tail(name)
                        diagnosis.append(
                            "updater-logs="
                            + redact_sensitive_text(tail or "(empty)")[-2000:]
                        )
                    except (RuntimeError, OSError, TimeoutError) as exc:
                        # Auxiliary log collection must never replace the
                        # established exhaustion with an unrelated failure.
                        diagnosis.append(
                            "updater-logs=unavailable:"
                            + redact_sensitive_text(str(exc))[:200]
                        )
                    recovery_hint = f"release job {key} (owner {name})"
                    if owner.startswith("host-update:"):
                        recovery_hint += (
                            "; delivery budget exhausted: start a new audited release with"
                            " ./tools/update-moonmind.sh (do not --resume this submission;"
                            " resume reuses the spent budget and its stopped container,"
                            " so it returns here without progressing)"
                        )
                    else:
                        recovery_hint += (
                            "; delivery budget exhausted: start a new audited release;"
                            " its retained workers still require release.reconcile"
                        )
                    raise RuntimeError(
                        "Release updater exhausted three deliveries without terminal evidence"
                        f" ({recovery_hint}; deliveries={deliveries}; "
                        + "; ".join(diagnosis)
                        + f"; inspect docker logs {name} and {directory / 'request.json'})"
                    )
                write_record(attempts_file, {"count": deliveries + 1})
                await docker("start", name)
            await asyncio.sleep(2)
    outcome = json.loads(result_file.read_text())
    if outcome.get("owner") != owner:
        raise ValueError("Release terminal receipt owner differs")
    if "error" in outcome:
        raise ToolFailure(
            error_code="DEPLOYMENT_RELEASE_FAILED",
            message=outcome["error"],
            retryable=False,
            details={"releaseJob": key, "recoveryOwner": name},
        )
    result = ToolResult(**outcome["result"])
    existing = await inspect_owned(name, owner)
    if existing and not existing["State"]["Running"]:
        await docker("rm", name)
    return result


async def verify_installed_fleet(runner, image, *, expected=None, attempts=60):
    """Wait until every installed fleet worker serves the requested release.

    Updates recreate the installed fleet in place, so readiness is asserted
    against that one fleet: exactly one container per fleet service, each
    reporting the expected release digest.
    """
    from moonmind.release_identity import installed_release
    from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

    expected = expected or installed_release()["digest"]
    for _ in range(attempts):
        ready = True
        for service in _FLEET_SERVICE_NAMES.values():
            found = await runner._run_compose_command(
                ("docker", "compose", "ps", "-q", service),
                requested_image=image,
            )
            _ensure_command_succeeded("inspect installed worker", found)
            identifiers = found["stdout"].split()
            if len(identifiers) != 1:
                ready = False
                break
            try:
                state = await worker_readiness(identifiers[0])
            except RuntimeError:
                ready = False
                break
            if not readiness_matches(state, expected):
                ready = False
                break
        if ready:
            return {
                "digest": expected,
                "status": "verified",
                "fleets": len(_FLEET_SERVICE_NAMES),
            }
        await asyncio.sleep(2)
    raise RuntimeError(
        "Installed fleet did not become ready for the requested release"
    )


async def migrate_omnigent(runner, owner, image, *, actor="release"):
    """Record, install, and verify the singular Omnigent release.

    Runs inside the primary success path so an omnigent migration failure
    blocks the release receipt exactly like a fleet verification failure.
    The migration only records the installed server/host digests, recreates
    the affected services onto them, and verifies the running server: it
    performs no policy/schedule bulk rewrites. Fresh managed attempts bind
    the installed target through normal admission while preserving authored
    execution choices and schedule identity.
    """
    from moonmind.workflows.skills.deployment_execution import (
        FileDesiredStateStore,
    )
    from moonmind.workflows.skills.omnigent_release import (
        migrate_omnigent_release,
        production_drivers,
    )

    store = FileDesiredStateStore(
        env_file_path=os.environ.get(
            "MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", ""
        ),
        json_file_path=os.environ.get(
            "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", ""
        )
        or None,
    )
    if not str(
        os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE") or ""
    ).strip():
        # No durable desired-state file: the singular record has nowhere to
        # live, so there is nothing to migrate. The receipt says so explicitly
        # instead of pretending alignment was verified.
        return {"status": "skipped", "reason": "no durable desired-state file"}
    return await migrate_omnigent_release(
        store=store,
        runner=runner,
        owner=owner,
        moonmind_image=image,
        drivers=production_drivers(
            runner=runner, moonmind_image=image, actor=actor
        ),
        actor=actor,
    )


async def verify_operator_access(image, urls, owner, *, expected_release=None):
    """Probe published origins through the daemon's declared host transport."""
    platform = (await docker("info", "--format", "{{.OperatingSystem}}")).strip()
    if not platform:
        raise ValueError("Docker host transport could not be established")
    # Desktop's host network is its Linux VM, not the operator's host. Its
    # explicit gateway crosses that boundary without changing HTTP/TLS authority.
    desktop = platform == "Docker Desktop"
    transport = "docker-host-gateway" if desktop else "host-network"
    from moonmind.workflows.skills.deployment_surface import validate_headers

    # Credentials remain deployment-owned. They are never copied into durable
    # request/receipt artifacts or Docker command arguments, nor minted here.
    credential_file = state_root().parent / "operator-http-headers.json"
    credentials = {}
    if credential_file.exists():
        if credential_file.is_symlink() or credential_file.stat().st_size > 65536:
            raise ValueError(
                "Operator credential file violates its bounded file authority"
            )
        credentials = json.loads(credential_file.read_bytes())
        if not isinstance(credentials, dict):
            raise ValueError(
                "Operator credential file must map exact origins to HTTP headers"
            )
    surfaces = []
    for url in urls:
        headers = validate_headers(credentials.get(url, {}))
        raw = await docker(
            "run",
            "--rm",
            "-i",
            "--network",
            "bridge" if desktop else "host",
            "--label",
            f"moonmind.release.owner={owner}",
            "--entrypoint",
            "python",
            image,
            "-m",
            "moonmind.workflows.skills.deployment_surface",
            url,
            *(["--docker-host-gateway"] if desktop else []),
            *(["--expected-release", expected_release] if expected_release else []),
            input_bytes=json.dumps(headers).encode(),
        )
        result = json.loads(raw)
        if (
            result.get("status") != "verified"
            or result.get("baseUrl") != url
            or result.get("transport") != transport
            or result.get("releaseDigest") != expected_release
            or set(result.get("checks", []))
            != {"healthz", "dashboard", "assets", "api/ui/info"}
        ):
            raise RuntimeError(
                "Published operator access lacks verified terminal evidence"
            )
        surfaces.append(result)
    if not surfaces:
        raise ValueError("Release cannot complete without operator access targets")
    return {"status": "verified", "surfaces": surfaces}


async def prepare_operator_access(runner, image, directory, owner, *, declared_urls=None):
    from moonmind.workflows.skills.deployment_surface import operator_urls

    path = directory / "operator-access-targets.json"
    if path.exists():
        recorded = json.loads(path.read_text())
        if recorded.get("owner") != owner:
            raise ValueError("Operator access target owner differs")
        urls = recorded["urls"]
    else:
        rendered = await runner._run_compose_command(
            ("docker", "compose", "config", "--format", "json"),
            requested_image=image,
            max_stdout_chars=None,
        )
        _ensure_command_succeeded("read operator access", rendered)
        urls = operator_urls(json.loads(rendered["stdout"]), declared_urls=declared_urls)
        recorded = reserve_record(path, {"owner": owner, "urls": urls})
        if recorded != {"owner": owner, "urls": urls}:
            raise ValueError("Operator access targets differ from the saved release")
    # Missing host-network capability or an unreachable origin stops before API
    # replacement. A retry keeps the original targets instead of changing scope.
    await verify_operator_access(image, urls, owner)
    return urls


async def _run_job_body(request_file):
    from api_service.db.base import get_async_session_context
    from moonmind.workflows.skills.deployment_execution import (
        TemporalDeploymentEvidenceWriter,
        _execution_ref_from_context,
    )
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.worker_runtime import (
        _build_deployment_update_executor,
    )

    record = json.loads(request_file.read_text())
    owner = record["authored"]["owner"]
    result_file = request_file.parent / "result.json"
    if result_file.exists():
        return
    context = {
        **record["authored"]["context"],
        "deployment_runner_mode": "ephemeral_updater_container",
    }
    from moonmind.release_identity import installed_release

    release = installed_release()
    expected_revision = record["authored"]["inputs"].get("sourceRevision")
    if release is None or (
        expected_revision and release.get("sourceRevision") != expected_revision
    ):
        raise ValueError("Release owner lacks the selected immutable source identity")
    executor = _build_deployment_update_executor()
    if executor is None:
        raise ValueError("Deployment execution substrate is unavailable")
    excluded = executor.excluded_services
    if CONTROL_SERVICE in excluded:
        raise ValueError(
            "A coherent versioned release must include the deployment worker; remove its obsolete self-preservation exclusion"
        )
    runner = replace(
        executor.runner,
        excluded_services=excluded,
        compose_file=(
            executor.runner.compose_file or "/app/release/docker-compose.yaml"
        ),
    )
    parsed = dict(record["authored"]["inputs"])
    repository, digest = record["image"].split("@", 1)
    parsed["image"] = {
        "repository": repository,
        "reference": digest,
        "resolvedDigest": digest,
    }
    primary_file = request_file.parent / "deployment-result.json"
    try:
        if primary_file.exists():
            primary = json.loads(primary_file.read_text())
            if primary["owner"] != owner:
                raise ValueError("Deployment result owner differs")
            result = ToolResult(**primary["result"])
        else:
            operator_targets = await prepare_operator_access(
                runner, record["image"], request_file.parent, owner,
                declared_urls=context.get("deployment_operator_urls"),
            )
            async with get_async_session_context() as session:
                executor = replace(
                    executor,
                    runner=runner,
                    excluded_services=excluded,
                    evidence_writer=TemporalDeploymentEvidenceWriter(
                        artifact_service=TemporalArtifactService(
                            TemporalArtifactRepository(session)
                        ),
                        principal=str(
                            context.get("deployment_evidence_principal")
                            or "system:deployment"
                        ),
                        execution_ref=_execution_ref_from_context(context),
                    ),
                )
                result = await executor.execute(parsed, context)
                if result.status != "COMPLETED":
                    write_record(
                        request_file.parent / "attempt-result.json",
                        {
                            "owner": owner,
                            "result": result.to_payload(),
                        },
                    )
                    reason = result.outputs.get("failure", {}).get("reason")
                    raise RuntimeError(
                        reason
                        or (
                            "Release verification did not establish completion. "
                            "The installed fleet is whatever this attempt left "
                            "running; no retained cohort exists and maintenance "
                            "does not resume this job. Re-run "
                            "./tools/update-moonmind.sh to start a fresh audited "
                            "release."
                        )
                    )
                if result.status == "COMPLETED":
                    readiness = await verify_installed_fleet(runner, record["image"])
                    readiness["operatorAccess"] = await verify_operator_access(
                        record["image"],
                        operator_targets,
                        owner,
                        expected_release=release["digest"],
                    )
                    readiness_ref = await executor.evidence_writer.write(
                        "installed-release-readiness", readiness
                    )
                    readiness_outputs = {
                        **result.outputs,
                        "releaseReadinessArtifactRef": readiness_ref,
                    }
                    if expected_revision:
                        readiness_outputs["sourceRevision"] = expected_revision
                    result = replace(result, outputs=readiness_outputs)
                    try:
                        omnigent_receipt = await migrate_omnigent(
                            runner,
                            owner,
                            record["image"]
                        )
                    except Exception as exc:
                        write_record(
                            request_file.parent / "attempt-result.json",
                            {
                                "owner": owner,
                                "result": result.to_payload(),
                            },
                        )
                        raise RuntimeError(
                            "Omnigent release migration did not establish "
                            f"completion ({exc}). The MoonMind fleet was "
                            "recreated and verified; only the Omnigent release "
                            "is unaligned. No retained cohort exists and "
                            "maintenance does not resume this job. Re-run "
                            "./tools/update-moonmind.sh to converge it."
                        ) from exc
                    result = replace(
                        result,
                        outputs={
                            **result.outputs,
                            "omnigentRelease": omnigent_receipt,
                        },
                    )
            write_record(primary_file, {"owner": owner, "result": result.to_payload()})
        # Recreate-in-place leaves no parallel cohort behind, so a verified
        # release has nothing left to drain or remove.
        if result.status == "COMPLETED" and expected_revision:
            # The terminal receipt is self-sufficient: it carries the verified
            # source revision alongside the digest and readiness evidence, so
            # recovery never depends on an unbound second record.
            result = replace(
                result,
                outputs={
                    **result.outputs,
                    "sourceRevision": expected_revision,
                },
            )
        write_record(result_file, {"owner": owner, "result": result.to_payload()})
    except Exception:
        raise


async def run_job(request_file):
    """The detached owner enforces the same durable deadline as its caller."""
    from moonmind.utils.logging import redact_sensitive_text

    request_file = request_file.resolve(strict=True)
    if not request_file.is_relative_to(state_root().resolve()):
        raise ValueError("Release request is outside the deployment state authority")
    record = json.loads(request_file.read_text())
    owner = record["authored"]["owner"]
    # The job runs only in the Linux deployment image. Kernel ownership prevents
    # an Activity retry and maintenance reconciliation from executing it twice.
    import fcntl

    with (request_file.parent / "owner.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        result_file = request_file.parent / "result.json"
        if result_file.exists():
            return
        attempts_file = request_file.parent / "attempts.json"
        attempts = (
            json.loads(attempts_file.read_text())["count"]
            if attempts_file.exists()
            else 0
        )
        error = "Release update exhausted its durable retry budget"
        while attempts < 3 and time.time() < record["deadline"]:
            attempts += 1
            write_record(attempts_file, {"count": attempts})
            try:
                async with asyncio.timeout(max(0, record["deadline"] - time.time())):
                    await _run_job_body(request_file)
                return
            except Exception as exc:
                error = bounded_diagnosis(
                    redact_sensitive_text(str(exc) or type(exc).__name__)
                )
                history = record_attempt_error(
                    request_file.parent, owner, attempts, error
                )
                error = release_failure_summary(history)
                if isinstance(exc, ValueError) or (
                    isinstance(exc, ToolFailure) and not exc.retryable
                ):
                    break
                await asyncio.sleep(
                    min(2**attempts, max(0, record["deadline"] - time.time()))
                )
        primary_file = request_file.parent / "deployment-result.json"
        if primary_file.exists():
            outcome = json.loads(primary_file.read_text())
            if outcome.get("owner") != owner:
                raise ValueError("Release primary receipt owner differs")
            # Recreate-in-place has no cleanup phase. Reporting one hid the
            # finalization error behind an operation that no longer exists
            # and named an owner with no recovery action.
            outcome["result"]["outputs"].update(
                recoveryOwner=owner, finalError=error
            )
        else:
            attempt_file = request_file.parent / "attempt-result.json"
            if attempt_file.exists():
                outcome = json.loads(attempt_file.read_text())
                if outcome.get("owner") != owner:
                    raise ValueError("Release attempt receipt owner differs")
                outcome["result"]["outputs"].update(
                    recoveryOwner=owner, finalError=error
                )
            else:
                outcome = {"owner": owner, "error": error}
        write_record(result_file, outcome)


async def submit(payload):
    from moonmind.workflows.temporal.worker_runtime import (
        _build_deployment_update_executor,
    )

    executor = _build_deployment_update_executor()
    if executor is None:
        raise ValueError("Deployment execution substrate is unavailable")
    executor = replace(
        executor,
        runner=replace(
            executor.runner,
            compose_file=executor.runner.compose_file
            or "/app/release/docker-compose.yaml",
        ),
    )
    result = await execute_detached(executor, payload["inputs"], payload["context"])
    print(json.dumps(result.to_payload(), sort_keys=True), flush=True)
    return 0 if result.status == "COMPLETED" else 1


if __name__ == "__main__":
    import sys

    if sys.argv[1] == "--submit":
        raise SystemExit(asyncio.run(submit(json.loads(sys.argv[2]))))
    asyncio.run(run_job(Path(sys.argv[1])))
