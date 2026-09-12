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

CONTROL_SERVICE = "temporal-worker-deployment-control"


async def docker(*args):
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 360)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(
            f"Docker {args[0]} failed: {stderr.decode(errors='replace')[:300]}"
        )
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


def readiness_matches(state, digest):
    if state.get("ready") is not True:
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


async def execute_detached(executor, inputs, context):
    """Launch once or reattach, without retaining self-recreation authority."""
    owner = str(context.get("idempotency_key") or context.get("workflow_id") or "")
    if not owner:
        raise ValueError("Release update requires a durable execution identity")
    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    directory = state_root() / key
    directory.mkdir(parents=True, exist_ok=True)
    request_file, result_file = directory / "request.json", directory / "result.json"
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
            "deadline": time.time() + 7200,
        }
        record = reserve_record(request_file, record)
        if record["authored"] != authored:
            raise ValueError("Release identity is already bound to different inputs")
    if not result_file.exists():
        await require_coherent_images(executor.runner, record["image"])
        existing = await inspect_owned(name, owner)
        if existing is None:
            launched = await executor.runner._run_compose_command(
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
                    str(request_file),
                ),
                requested_image=record["image"],
            )
            # Reconcile the daemon even if Compose lost its acknowledgement.
            existing = await inspect_owned(name, owner)
            if existing is None:
                _ensure_command_succeeded("launch updater", launched)
                raise RuntimeError("Updater launch has no remotely verified owner")
        if existing["Image"] != record["imageId"]:
            raise ValueError("Updater container image differs from the pinned release")
        # Compose services normally restart forever. The bounded job's terminal
        # receipt owns completion, so the one-off must not inherit that policy.
        await docker("update", "--restart=no", name)
        while not result_file.exists():
            if time.time() >= record["deadline"]:
                raise RuntimeError(
                    "Release update exhausted its durable two-hour budget"
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
                    raise RuntimeError(
                        "Release updater exhausted three deliveries without terminal evidence"
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


class ReleaseCohort:
    """Qualify every candidate queue while the current fleet remains alive."""

    def __init__(self, runner, directory, owner):
        self.runner, self.directory, self.owner = runner, directory, owner
        self.names = []

    async def preserve_previous(self, previous, deployment, candidate):
        """Keep the exact previous image polling until Temporal drains it."""
        if previous in {"", "__unversioned__", f"{deployment}.{candidate}"}:
            return
        from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

        record_file = self.directory / "retained.json"
        expected_digest = previous.removeprefix(deployment + ".")
        if record_file.exists():
            retained = json.loads(record_file.read_text())
            if retained["owner"] != self.owner or retained["version"] != previous:
                raise ValueError("Retained release authority differs")
        else:
            images = set()
            for service in _FLEET_SERVICE_NAMES.values():
                found = await self.runner._run_compose_command(
                    ("docker", "compose", "ps", "-q", service)
                )
                _ensure_command_succeeded("inspect previous release", found)
                identifiers = found["stdout"].split()
                if len(identifiers) != 1 or not readiness_matches(
                    await worker_readiness(identifiers[0]), expected_digest
                ):
                    raise ValueError(
                        "Previous release has no coherent live worker owner"
                    )
                observed = json.loads(await docker("inspect", identifiers[0]))[0]
                images.add(observed["Image"])
            if len(images) != 1:
                raise ValueError("Previous release spans different images")
            retained = {
                "owner": self.owner,
                "version": previous,
                "image": images.pop(),
                "retired": [],
            }
            write_record(record_file, retained)
        retained_runner = self.runner
        if self.runner.compose_file == "/app/release/docker-compose.yaml":
            retained_compose = self.directory / "retained-compose.yaml"
            if not retained_compose.exists():
                content = await docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "cat",
                    retained["image"],
                    "/app/release/docker-compose.yaml",
                )
                _atomic_write_bytes(retained_compose, content.encode())
            retained_runner = replace(self.runner, compose_file=str(retained_compose))
        for fleet, service in _FLEET_SERVICE_NAMES.items():
            name = f"mm-retained-{self.directory.name[:16]}-{fleet.replace('_', '-')}"
            existing = await inspect_owned(name, self.owner)
            if existing is None:
                launched = await retained_runner._run_compose_command(
                    (
                        "docker",
                        "compose",
                        "run",
                        "-d",
                        "--no-deps",
                        "--name",
                        name,
                        "--label",
                        f"moonmind.release.owner={self.owner}",
                        "-e",
                        "MOONMIND_RELEASE_QUALIFICATION=1",
                        service,
                    ),
                    requested_image=retained["image"],
                )
                existing = await inspect_owned(name, self.owner)
                if existing is None:
                    _ensure_command_succeeded("retain previous release", launched)
                    raise RuntimeError(
                        "Previous release replacement has no verified owner"
                    )
            if existing["Image"] != retained["image"]:
                raise ValueError("Retained worker image differs from previous release")
            for attempt in range(60):
                try:
                    if readiness_matches(await worker_readiness(name), expected_digest):
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(2)
            else:
                raise RuntimeError(
                    "Previous release could not retain compatible pollers"
                )

    async def qualify_api(self, image):
        name = f"mm-candidate-{self.directory.name[:16]}-api"
        if name not in self.names:
            self.names.append(name)
        existing = await inspect_owned(name, self.owner)
        if existing is None:
            launched = await self.runner._run_compose_command(
                (
                    "docker",
                    "compose",
                    "run",
                    "-d",
                    "--no-deps",
                    "--name",
                    name,
                    "--label",
                    f"moonmind.release.owner={self.owner}",
                    "api",
                ),
                requested_image=image,
            )
            existing = await inspect_owned(name, self.owner)
            if existing is None:
                _ensure_command_succeeded("launch candidate API", launched)
                raise RuntimeError("Candidate API launch has no verified owner")
        expected_image = json.loads((self.directory / "request.json").read_text())[
            "imageId"
        ]
        if existing["Image"] != expected_image:
            raise ValueError("Candidate API image differs from selected release")
        for attempt in range(60):
            try:
                await docker(
                    "exec",
                    name,
                    "python",
                    "-m",
                    "moonmind.workflows.skills.deployment_surface",
                    "http://localhost:8000",
                )
                return
            except RuntimeError:
                await asyncio.sleep(2)
        raise RuntimeError(
            "Candidate API did not pass health, dashboard, asset and read-only API qualification"
        )

    async def qualify(self, image):
        from moonmind.config.settings import settings
        from moonmind.release_identity import installed_release
        from moonmind.workflows.temporal.client import get_temporal_client
        from moonmind.workflows.temporal.release_routing import (
            current_version,
            promote_version,
            routing_snapshot,
        )
        from moonmind.workflows.temporal.workers import (
            _FLEET_SERVICE_NAMES,
            build_all_worker_topologies,
        )

        release = installed_release()
        if release is None:
            raise ValueError("Release updater must execute from an immutable image")
        await require_coherent_images(self.runner, image)
        deployment = (
            os.environ.get("TEMPORAL_WORKER_DEPLOYMENT_NAME")
            or "moonmind-workflow-fleet"
        )
        topologies = build_all_worker_topologies()
        client = await get_temporal_client(
            settings.temporal.address, settings.temporal.namespace
        )
        from temporalio.service import RPCError, RPCStatusCode

        try:
            snapshot = await routing_snapshot(client, deployment)
            previous = current_version(snapshot)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            previous = "__unversioned__"
        record_file = self.directory / "routing.json"
        if record_file.exists():
            prior = json.loads(record_file.read_text())
            if prior["candidate"] != release["digest"]:
                raise ValueError("Release candidate changed within one update")
            previous = prior["previous"]
        else:
            write_record(
                record_file,
                {
                    "previous": previous,
                    "candidate": release["digest"],
                    "deployment": deployment,
                },
            )
        await self.preserve_previous(previous, deployment, release["digest"])
        for topology in topologies:
            service = _FLEET_SERVICE_NAMES[topology.fleet]
            name = f"mm-candidate-{self.directory.name[:16]}-{topology.fleet.replace('_', '-')}"
            self.names.append(name)
            existing = await inspect_owned(name, self.owner)
            if existing is None:
                launched = await self.runner._run_compose_command(
                    (
                        "docker",
                        "compose",
                        "run",
                        "-d",
                        "--no-deps",
                        "--name",
                        name,
                        "--label",
                        f"moonmind.release.owner={self.owner}",
                        "-e",
                        "MOONMIND_RELEASE_QUALIFICATION=1",
                        service,
                    ),
                    requested_image=image,
                )
                existing = await inspect_owned(name, self.owner)
                if existing is None:
                    _ensure_command_succeeded("launch candidate", launched)
                    raise RuntimeError("Candidate launch has no verified owner")
        target = f"{deployment}.{release['digest']}"
        for attempt in range(90):
            try:
                observed = current_version(await routing_snapshot(client, deployment))
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                await asyncio.sleep(2)
                continue
            if observed not in {previous, target}:
                raise ValueError("Another release changed routing before promotion")
            # Wait for all container readiness probes before the pinned canary.
            states = []
            for name in self.names:
                try:
                    readiness = await worker_readiness(name)
                except RuntimeError:
                    readiness = {}
                states.append(readiness)
            if all(readiness_matches(row, release["digest"]) for row in states):
                await self.qualify_api(image)
                return await promote_version(
                    client,
                    deployment=deployment,
                    build_id=release["digest"],
                    expected_current=previous,
                    task_queue=topologies[0].task_queues[0],
                    task_queues=tuple(
                        dict.fromkeys(
                            queue for item in topologies for queue in item.task_queues
                        )
                    ),
                    canary_id=f"mm-release-canary-{self.directory.name}",
                )
            await asyncio.sleep(2)
        raise RuntimeError(
            "Candidate fleet did not become ready; current routing was retained"
        )

    async def verify_installed(self, image, *, expected=None, attempts=60):
        from moonmind.release_identity import installed_release
        from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

        expected = expected or installed_release()["digest"]
        for attempt in range(attempts):
            ready = True
            for service in _FLEET_SERVICE_NAMES.values():
                found = await self.runner._run_compose_command(
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
            "Installed worker cohort did not become ready; candidate workers retain routing"
        )

    async def cleanup(self):
        for name in self.names:
            existing = await inspect_owned(name, self.owner)
            if existing:
                await docker("stop", "--time", "300", name)
                observed = await inspect_owned(name, self.owner)
                if observed and observed["State"]["Running"]:
                    raise RuntimeError("Candidate worker did not drain")
                await docker("rm", name)


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
    cohort = ReleaseCohort(runner, request_file.parent, owner)
    context["release_cohort"] = cohort
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
            from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES

            cohort.names = [
                f"mm-candidate-{request_file.parent.name[:16]}-{fleet.replace('_', '-')}"
                for fleet in _FLEET_SERVICE_NAMES
            ] + [f"mm-candidate-{request_file.parent.name[:16]}-api"]
        else:
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
                        or "Release verification did not establish completion; retained workers own recovery"
                    )
                if result.status == "COMPLETED":
                    readiness = await cohort.verify_installed(record["image"])
                    from moonmind.workflows.skills.deployment_surface import (
                        verify_surface,
                    )

                    rendered = await runner._run_compose_command(
                        ("docker", "compose", "config", "--format", "json"),
                        requested_image=record["image"],
                        max_stdout_chars=None,
                    )
                    _ensure_command_succeeded("read operator access", rendered)
                    operator_url = (
                        json.loads(rendered["stdout"])["services"]["api"]
                        .get("environment", {})
                        .get("MOONMIND_PUBLIC_BASE_URL")
                    )
                    readiness["operatorAccess"] = (
                        (await asyncio.to_thread(verify_surface, operator_url))
                        if operator_url
                        else {
                            "status": "unknown",
                            "reason": "operator URL is not declared in deployment configuration",
                        }
                    )
                    readiness_ref = await executor.evidence_writer.write(
                        "installed-release-readiness", readiness
                    )
                    result = replace(
                        result,
                        outputs={
                            **result.outputs,
                            "releaseReadinessArtifactRef": readiness_ref,
                        },
                    )
            write_record(primary_file, {"owner": owner, "result": result.to_payload()})
        if result.status == "COMPLETED":
            # Cleanup is auxiliary to the verified deployment. Preserve primary
            # success and durable cleanup ownership if bounded cleanup exhausts.
            cleanup_error = None
            for attempt in range(3):
                try:
                    await cohort.cleanup()
                    cleanup_error = None
                    break
                except RuntimeError as exc:
                    cleanup_error = str(exc)
                    await asyncio.sleep(attempt + 1)
            if cleanup_error:
                from moonmind.utils.logging import redact_sensitive_text

                result = replace(
                    result,
                    outputs={
                        **result.outputs,
                        "cleanupPending": True,
                        "cleanupOwner": owner,
                        "cleanupReason": redact_sensitive_text(cleanup_error)[:300],
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
                error = redact_sensitive_text(str(exc) or type(exc).__name__)[:1000]
                write_record(
                    request_file.parent / "last-error.json",
                    {"owner": owner, "attempt": attempts, "error": error},
                )
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
            outcome["result"]["outputs"].update(cleanupPending=True, cleanupOwner=owner)
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
