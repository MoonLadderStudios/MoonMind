"""Exact-image N-way concurrency rows for the generic Omnigent plane.

Source issue: MoonLadderStudios/MoonMind#3885 (exact-Docker layer).

This module *executes* the exact-Docker layer. It launches ``N`` run-dedicated
hosts from the digest-pinned host image through the production side-effect
owners — :class:`~moonmind.omnigent.host_services.launcher.DockerOmnigentHostLauncher`
and
:class:`~moonmind.omnigent.host_services.cleanup.DockerOmnigentHostCleanupService`
over :class:`~moonmind.omnigent.host_services.docker_backend.DockerCommandBackend`
— holds every one of them simultaneously live on one real daemon, and publishes
the observation the qualification runner files as this layer's row.

Two things are deliberate:

* **It never calls the qualification runner.** An owning test that asked the
  runner to run the layer would ask the runner to run this file again. The
  runner's own record contract is asserted in
  ``tests/unit/omnigent/test_concurrency_qualification.py``, which is not an
  owning test and therefore never re-enters the layer.
* **Overlap is observed, not asserted.** Each host records the window between
  the moment the daemon reported it ``running`` and the moment its cleanup
  authority returned, and no host is torn down until every host has been
  observed running. ``N`` hosts launched one after another would sweep to a
  peak of 1 and fail the evidence contract.

Without a daemon, without the digest-pinned image, or without a host server
endpoint, this layer produces no evidence and the runner records an
``unavailable`` row. That row never qualifies, so a missing environment can
never be mistaken for a pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.concurrency_qualification import (
    CleanupScanEntry,
    CleanupScanReport,
    ConcurrencyQualificationLayer,
    ExecutionOverlapSample,
    ObservedOverlapEvidence,
    publish_observed_overlap,
    requested_concurrency_level,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HostClass,
    get_launch_policy,
)
from moonmind.omnigent.host_ports import (
    HostLaunchSpec,
    expected_omnigent_host_id,
    host_correlation_identity,
)
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService

pytestmark = [pytest.mark.integration]

#: The default level when the runner did not select one, so a developer
#: invoking this file directly still exercises real concurrency rather than a
#: single host.
DEFAULT_EXACT_DOCKER_LEVEL = 2

HOST_CLASS_REF = "omnigent-opencode@1"
LAUNCH_POLICY_REF = "omnigent-on-demand@1"
PLAN_REF = "omnigent-execution-plan:sha256:" + "c" * 64

#: How long one exact host may take to reach ``running`` on a cold daemon.
_RUNNING_TIMEOUT_SECONDS = 120.0
_RUNNING_POLL_SECONDS = 0.5


def _host_image() -> str:
    return os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "").strip()


def _server_url() -> str:
    return os.getenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "").strip()


def _network_ref() -> str:
    """Return the Docker network the exact hosts attach to.

    ``bridge`` is the daemon's own default network, so the documented default
    path needs no deployment-specific value; a deployment with an egress
    profile names its own network instead.
    """

    return os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_NETWORK", "").strip() or "bridge"


def _exact_docker_environment_reason() -> str:
    """Return why the exact-image layer cannot run here, or ``""``."""

    if not _host_image():
        return "no digest-pinned exact host image is configured"
    if not _server_url():
        return "MOONMIND_OMNIGENT_HOST_SERVER_URL names no host server endpoint"
    if shutil.which("docker") is None:
        return "the docker client is not installed on this runner"
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the docker daemon is unreachable: {exc}"
    if completed.returncode != 0:
        return "the docker daemon did not report a server version"
    return ""


def _host_class(image_ref: str) -> HostClass:
    """Build the Host Class for the exact image under test.

    Only the image is environment-supplied. Everything else is the declared
    generic OpenCode host contract, so a row cannot pass by relaxing the host
    definition it claims to have qualified.
    """

    return HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": image_ref,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": (
                        "omnigent-harness-implementation:sha256:" + "3" * 64
                    ),
                    "runtimeDependencies": [
                        {"name": "opencode", "version": "1.18.11"}
                    ],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["none@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


class _ExactHost:
    """One run-dedicated exact host and the window it was observed live for."""

    def __init__(self, run_ref: str, spec: HostLaunchSpec) -> None:
        self.run_ref = run_ref
        self.spec = spec
        self.launch: dict[str, object] = {}
        self.started_at = 0.0
        self.ended_at = 0.0


def _launch_spec(
    *,
    run_ref: str,
    image_ref: str,
    server_url: str,
    workspace_path: str,
    skills_path: str,
    limits: dict[str, int],
    runtime: dict[str, object],
) -> HostLaunchSpec:
    """Build one run's launch spec exactly as ``GenericOmnigentHostRuntime`` does.

    The identity derivations (correlation name, state volume digest, expected
    Omnigent host id) are the production ones, so two runs sharing a container
    name, a state volume, or a host id would show up here rather than in a
    deployment.
    """

    runtime_binding_id = f"omnigent-runtime-binding:{run_ref}"
    host_lease_ref = f"omnigent-host-lease:{run_ref}"
    correlation = host_correlation_identity(host_lease_ref)
    state_digest = hashlib.sha256(
        f"{runtime_binding_id}\0{host_lease_ref}".encode("utf-8")
    ).hexdigest()[:32]
    return HostLaunchSpec.model_validate(
        {
            "executionPlanRef": PLAN_REF,
            "stepExecutionId": f"mm:concurrency:{run_ref}:1",
            "runtimeBindingId": runtime_binding_id,
            "hostLeaseRef": host_lease_ref,
            "hostLeaseGeneration": 1,
            "hostClassRef": HOST_CLASS_REF,
            "imageRef": image_ref,
            "serverEndpointRef": "default",
            "serverUrl": server_url,
            "networkRef": _network_ref(),
            "limits": limits,
            "runtime": runtime,
            "correlationName": correlation,
            "expectedOmnigentHostId": expected_omnigent_host_id(host_lease_ref, 1),
            "workspaceAttachment": {
                "kind": "bind",
                "sourceRef": workspace_path,
                "targetPath": "/workspaces/run",
                "accessMode": "read-write",
            },
            "skillAttachment": {
                "kind": "bind",
                "sourceRef": skills_path,
                "targetPath": "/opt/moonmind-skills",
                "accessMode": "read-only",
            },
            "toolAttachments": [],
            "credentialAttachments": [],
            "githubCredentialAttachment": None,
            "controlAttachment": None,
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": f"mm-omnigent-state-{state_digest}",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": {
                "moonmind.owner": "generic-omnigent-host",
                "moonmind.execution_plan_ref": PLAN_REF,
                "moonmind.runtime_binding_id": runtime_binding_id,
                "moonmind.host_lease_ref": host_lease_ref,
                "moonmind.host_lease_generation": "1",
            },
        }
    )


async def _await_running(
    backend: DockerCommandBackend, container_name: str
) -> None:
    """Block until the daemon reports the container ``running``.

    A host that exited is not a concurrent host, so this fails with the
    container's own status and log tail rather than counting a dead container
    towards the observed peak.
    """

    deadline = time.monotonic() + _RUNNING_TIMEOUT_SECONDS
    status = ""
    while time.monotonic() < deadline:
        code, out, _err = await backend.run(
            [
                "docker",
                "container",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container_name,
            ],
            check=False,
        )
        status = out.strip() if code == 0 else status
        if status == "running":
            return
        if status in {"exited", "dead"}:
            break
        await asyncio.sleep(_RUNNING_POLL_SECONDS)
    _code, logs, log_err = await backend.run(
        ["docker", "logs", "--tail", "40", container_name], check=False
    )
    pytest.fail(
        f"exact host {container_name} never reached running (status={status!r}): "
        f"{(logs or log_err)[:1024]}"
    )


async def _cleanup_scan(
    backend: DockerCommandBackend, hosts: list[_ExactHost]
) -> CleanupScanReport:
    """Return the real post-run scan of every resource these hosts owned."""

    entries: list[CleanupScanEntry] = []
    for host in hosts:
        container = str(host.spec.correlationName)
        code, _out, _err = await backend.run(
            ["docker", "container", "inspect", "--format", "{{.Id}}", container],
            check=False,
        )
        entries.append(
            CleanupScanEntry(
                resource_ref=container, kind="container", resolved=code != 0
            )
        )
        volume = str(host.spec.stateAttachment["sourceRef"])
        code, _out, _err = await backend.run(
            ["docker", "volume", "inspect", "--format", "{{.Name}}", volume],
            check=False,
        )
        entries.append(
            CleanupScanEntry(resource_ref=volume, kind="volume", resolved=code != 0)
        )
    return CleanupScanReport(scanned_at=datetime.now(UTC), entries=tuple(entries))


@pytest.mark.asyncio
async def test_exact_images_run_the_required_concurrency_level(tmp_path) -> None:
    """Run the exact-image row at the level the qualification runner selected."""

    reason = _exact_docker_environment_reason()
    if reason:
        # The runner already records this layer as ``unavailable`` when the
        # environment is absent, so skipping here loses no evidence: the record
        # still shows the level was never observed on real images.
        pytest.skip(f"exact-image concurrency layer unavailable: {reason}")

    level = requested_concurrency_level(default=DEFAULT_EXACT_DOCKER_LEVEL)
    image_ref = _host_image()
    server_url = _server_url()
    host_class = _host_class(image_ref)
    launch_policy = get_launch_policy(LAUNCH_POLICY_REF)
    backend = DockerCommandBackend()
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url=server_url,
    )
    cleanup_service = DockerOmnigentHostCleanupService(backend)

    wave_ref = uuid.uuid4().hex[:12]
    hosts: list[_ExactHost] = []
    for index in range(level):
        run_ref = f"{wave_ref}-{index}"
        workspace = tmp_path / run_ref / "repo"
        skills = tmp_path / run_ref / "skills"
        workspace.mkdir(parents=True)
        skills.mkdir(parents=True)
        hosts.append(
            _ExactHost(
                run_ref,
                _launch_spec(
                    run_ref=run_ref,
                    image_ref=image_ref,
                    server_url=server_url,
                    workspace_path=str(workspace),
                    skills_path=str(skills),
                    limits=dict(launch_policy.limits),
                    runtime=dict(host_class.runtime),
                ),
            )
        )

    origin = time.monotonic()
    # No host is torn down until every host has been observed running, so the
    # windows below genuinely overlap instead of merely being adjacent.
    all_running = asyncio.Barrier(level)

    async def _run_one(host: _ExactHost) -> None:
        try:
            host.launch = await launcher.launch(
                spec=host.spec,
                host_class=host_class,
                launch_policy=launch_policy,
                credential_handles=[],
            )
            await _await_running(backend, str(host.spec.correlationName))
            host.started_at = time.monotonic() - origin
        except BaseException:
            # Release the peers rather than leaving them parked on a barrier
            # that can never fill. Their containers still need teardown, and a
            # deadlocked wave would strand every one of them.
            all_running.abort()
            raise
        await all_running.wait()

    outcomes = await asyncio.gather(
        *(_run_one(host) for host in hosts), return_exceptions=True
    )
    # Every host is offered to its cleanup authority, including the hosts whose
    # peers failed. Teardown is reported after the wave so one stranded
    # container cannot prevent the others from being reclaimed.
    cleanup_errors: list[BaseException] = []
    for host in hosts:
        try:
            await cleanup_service.cleanup(
                container_name=str(host.spec.correlationName),
                host_lease_ref=str(host.spec.hostLeaseRef),
                host_lease_generation=int(host.spec.hostLeaseGeneration),
                state_volume_ref=str(host.spec.stateAttachment["sourceRef"]),
            )
        except BaseException as exc:  # noqa: BLE001 - every host is reclaimed
            cleanup_errors.append(exc)
        host.ended_at = time.monotonic() - origin

    launch_failures = [item for item in outcomes if isinstance(item, BaseException)]
    if launch_failures:
        raise launch_failures[0]
    if cleanup_errors:
        raise cleanup_errors[0]

    # Every execution received its own container, container id, state volume,
    # and addressable Omnigent host id.
    for key in ("containerName", "containerId", "stateVolumeRef"):
        values = [str(host.launch[key]) for host in hosts]
        assert len(set(values)) == level, f"{key} was shared between exact hosts"
    assert (
        len({str(host.spec.expectedOmnigentHostId) for host in hosts}) == level
    ), "two exact hosts shared one addressable Omnigent host id"

    scan = await _cleanup_scan(backend, hosts)
    assert scan.zero_leak, [entry.resource_ref for entry in scan.unresolved]

    overlap = ObservedOverlapEvidence(
        requested_level=level,
        effective_limit=level,
        barrier_synchronized=True,
        samples=tuple(
            ExecutionOverlapSample(
                execution_ref=host.run_ref,
                started_at=host.started_at,
                ended_at=max(host.started_at, host.ended_at),
            )
            for host in hosts
        ),
    )
    assert overlap.observed_peak == level

    publish_observed_overlap(ConcurrencyQualificationLayer.exact_docker, overlap)
