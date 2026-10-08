"""Exact-image N-way concurrency rows for the generic Omnigent plane.

Source issue: MoonLadderStudios/MoonMind#3885 (exact-Docker layer).

This module *executes* the exact-Docker layer. It launches ``N`` run-dedicated
hosts from the digest-pinned host image through the production side-effect
owners — :class:`~moonmind.omnigent.host_services.launcher.DockerOmnigentHostLauncher`,
:class:`~moonmind.omnigent.host_services.registration.OmnigentHostRegistrationService`
and
:class:`~moonmind.omnigent.host_services.cleanup.DockerOmnigentHostCleanupService`
over :class:`~moonmind.omnigent.host_services.docker_backend.DockerCommandBackend`
— holds every one of them simultaneously live on one real daemon, and publishes
the observation the qualification runner files as this layer's row.

Three things are deliberate:

* **It never calls the qualification runner.** An owning test that asked the
  runner to run the layer would ask the runner to run this file again. The
  runner's own record contract is asserted in
  ``tests/unit/omnigent/test_concurrency_qualification.py``, which is not an
  owning test and therefore never re-enters the layer.
* **A live container is not an execution.** A host that is up but cannot
  register with the configured Omnigent server, or registers as some other
  identity, has crossed no authority handoff: ``N`` idle or retrying containers
  would otherwise publish a passing row for an N-way path that is unusable. The
  window a host contributes to the observed peak therefore opens only after the
  *production* registration authority returns that host's expected addressable
  Omnigent host id.
* **Overlap is observed, not asserted, and it is observed twice.** Each host
  records the window between the moment it registered and the moment its
  cleanup authority returned, and no host is torn down until every host in its
  wave has registered. ``N`` hosts launched one after another would sweep to a
  peak of 1 and fail the evidence contract. The level is then run again, and the
  row is gated on the bounded-growth report across both waves, so image-level
  growth that only appears after teardown and relaunch — accumulating
  registrations, volumes, control latency — fails the row instead of hiding
  behind a single wave.

Without a daemon, without the digest-pinned image, or without the Omnigent
server endpoint and owner the registration authority needs, this layer produces
no evidence and the runner records an ``unavailable`` row. That row never
qualifies, so a missing environment can never be mistaken for a pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.concurrency_qualification import (
    EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS,
    CleanupScanEntry,
    CleanupScanReport,
    ConcurrencyQualificationLayer,
    ExecutionOverlapSample,
    ObservedOverlapEvidence,
    RepeatedWaveReport,
    WaveObservation,
    observed_peak_overlap,
    publish_observed_overlap,
    requested_concurrency_level,
    unsatisfied_exact_docker_environment,
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
from moonmind.omnigent.host_services.registration import (
    OmnigentHostRegistrationService,
)
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_host_runner_token,
    resolved_server_url,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [pytest.mark.integration]

#: The default level when the runner did not select one, so a developer
#: invoking this file directly still exercises real concurrency rather than a
#: single host.
DEFAULT_EXACT_DOCKER_LEVEL = 2

#: Waves per level. Bounded growth needs at least two observations of the same
#: level to compare, and this layer pays for each one in real containers, so it
#: runs the minimum the report will accept.
EXACT_DOCKER_WAVES = 2

HOST_CLASS_REF = "omnigent-opencode@1"
HARNESS_ID = "opencode-native"
LAUNCH_POLICY_REF = "omnigent-on-demand@1"
PLAN_REF = "omnigent-execution-plan:sha256:" + "c" * 64

#: How long one exact host may take to reach ``running`` on a cold daemon.
_RUNNING_TIMEOUT_SECONDS = 120.0
_RUNNING_POLL_SECONDS = 0.5

#: Docker says exactly this when the resource it was asked about does not
#: exist. Every other non-zero exit is an operational failure of the scan
#: itself, which proves nothing about whether the resource is still there.
_DOCKER_ABSENT_MARKER = "no such"


def _host_image() -> str:
    return os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "").strip()


def _host_server_url() -> str:
    return os.getenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "").strip()


def _expected_host_owner() -> str:
    return os.getenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER", "").strip()


def _network_ref() -> str:
    """Return the Docker network the exact hosts attach to.

    ``bridge`` is the daemon's own default network, so the documented default
    path needs no deployment-specific value; a deployment with an egress
    profile names its own network instead.
    """

    return os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_NETWORK", "").strip() or "bridge"


def _exact_docker_environment_reason() -> str:
    """Return why the exact-image layer cannot run here, or ``""``."""

    unsatisfied = unsatisfied_exact_docker_environment()
    if unsatisfied:
        return "the layer is not configured: " + ", ".join(unsatisfied)
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
                    "harnessId": HARNESS_ID,
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
        self.registration: dict[str, object] = {}
        self.started_at = 0.0
        self.ended_at = 0.0


@dataclass
class _WaveResult:
    """What one wave of ``level`` exact hosts produced."""

    hosts: list[_ExactHost]
    samples: tuple[ExecutionOverlapSample, ...] = ()
    scan: CleanupScanReport | None = None
    launch_seconds: float = 0.0
    registration_seconds: float = 0.0
    wait_seconds: float = 0.0
    control_seconds: float = 0.0
    cleanup_seconds: float = 0.0
    registration_requests: int = 0
    diagnostics: list[str] = field(default_factory=list)

    @property
    def observed_peak(self) -> int:
        return observed_peak_overlap(self.samples)


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


async def _scan_entry(
    backend: DockerCommandBackend,
    argv: list[str],
    *,
    resource_ref: str,
    kind: str,
) -> tuple[CleanupScanEntry, str]:
    """Return one scanned resource plus any reason the scan itself failed.

    Only Docker's own "no such ..." answer proves a resource is gone. A daemon
    that became unreachable, an authorization change, or any other failure of
    the scan establishes nothing about the resource, so the entry stays
    unresolved and the reason travels with it instead of being read as a clean
    teardown.
    """

    code, _out, err = await backend.run(argv, check=False)
    if code == 0:
        return CleanupScanEntry(resource_ref=resource_ref, kind=kind, resolved=False), ""
    if _DOCKER_ABSENT_MARKER in err.lower():
        return CleanupScanEntry(resource_ref=resource_ref, kind=kind, resolved=True), ""
    return (
        CleanupScanEntry(resource_ref=resource_ref, kind=kind, resolved=False),
        f"{kind} {resource_ref}: scan failed ({err.strip()[:160] or f'exit {code}'})",
    )


async def _cleanup_scan(
    backend: DockerCommandBackend, hosts: list[_ExactHost]
) -> tuple[CleanupScanReport, list[str]]:
    """Return the real post-run scan of every resource these hosts owned."""

    entries: list[CleanupScanEntry] = []
    scan_errors: list[str] = []
    for host in hosts:
        container = str(host.spec.correlationName)
        entry, error = await _scan_entry(
            backend,
            ["docker", "container", "inspect", "--format", "{{.Id}}", container],
            resource_ref=container,
            kind="container",
        )
        entries.append(entry)
        if error:
            scan_errors.append(error)
        volume = str(host.spec.stateAttachment["sourceRef"])
        entry, error = await _scan_entry(
            backend,
            ["docker", "volume", "inspect", "--format", "{{.Name}}", volume],
            resource_ref=volume,
            kind="volume",
        )
        entries.append(entry)
        if error:
            scan_errors.append(error)
    report = CleanupScanReport(scanned_at=datetime.now(UTC), entries=tuple(entries))
    return report, scan_errors


async def _run_wave(
    *,
    wave_index: int,
    level: int,
    origin: float,
    tmp_path,
    image_ref: str,
    host_server_url: str,
    host_class: HostClass,
    launch_policy,
    backend: DockerCommandBackend,
    launcher: DockerOmnigentHostLauncher,
    registration: OmnigentHostRegistrationService,
    cleanup_service: DockerOmnigentHostCleanupService,
) -> _WaveResult:
    """Launch, register, hold and reclaim one wave of ``level`` exact hosts.

    ``origin`` is the whole test's timeline, shared by every wave, so the
    published samples describe when each host was live relative to all the
    others. Restarting the clock per wave would make two sequential waves look
    simultaneous and inflate the observed peak to twice the level.
    """

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
                    server_url=host_server_url,
                    workspace_path=str(workspace),
                    skills_path=str(skills),
                    limits=dict(launch_policy.limits),
                    runtime=dict(host_class.runtime),
                ),
            )
        )

    wave_started = time.monotonic()
    result = _WaveResult(hosts=hosts)
    # No host is torn down until every host in the wave has *registered*, so
    # the windows below genuinely overlap at the execution boundary instead of
    # merely being adjacent — or worse, merely being alive.
    all_registered = asyncio.Barrier(level)
    phase_lock = asyncio.Lock()

    async def _run_one(host: _ExactHost) -> None:
        try:
            launch_started = time.monotonic()
            host.launch = await launcher.launch(
                spec=host.spec,
                host_class=host_class,
                launch_policy=launch_policy,
                credential_handles=[],
            )
            await _await_running(backend, str(host.spec.correlationName))
            registered_from = time.monotonic()
            # The production authority handoff. A container that is merely up
            # has attempted nothing: this returns only once the configured
            # Omnigent server reports *this* host id, owned by this deployment,
            # with the declared harness ready.
            host.registration = await registration.wait_for_registration(
                correlation_name=str(host.spec.correlationName),
                harness_id=HARNESS_ID,
                credentialless=True,
                expected_host_id=str(host.spec.expectedOmnigentHostId),
            )
            registered_at = time.monotonic()
            host.started_at = registered_at - origin
            async with phase_lock:
                result.launch_seconds = max(
                    result.launch_seconds, registered_from - launch_started
                )
                result.registration_seconds = max(
                    result.registration_seconds, registered_at - registered_from
                )
                result.registration_requests += 1
        except BaseException:
            # Release the peers rather than leaving them parked on a barrier
            # that can never fill. Their containers still need teardown, and a
            # deadlocked wave would strand every one of them. ``abort`` is a
            # coroutine: discarding it here is what leaves the waiters blocked
            # until the job's own timeout.
            await all_registered.abort()
            raise
        waited_from = time.monotonic()
        await all_registered.wait()
        async with phase_lock:
            result.wait_seconds = max(
                result.wait_seconds, time.monotonic() - waited_from
            )

    outcomes = await asyncio.gather(
        *(_run_one(host) for host in hosts), return_exceptions=True
    )
    # Every host is offered to its cleanup authority, including the hosts whose
    # peers failed. Teardown is reported after the wave so one stranded
    # container cannot prevent the others from being reclaimed.
    cleanup_started = time.monotonic()
    # Every control-plane operation from the first launch to the moment the
    # last host released the barrier.
    result.control_seconds = cleanup_started - wave_started
    cleanup_errors: list[Exception] = []
    for host in hosts:
        try:
            await cleanup_service.cleanup(
                container_name=str(host.spec.correlationName),
                host_lease_ref=str(host.spec.hostLeaseRef),
                host_lease_generation=int(host.spec.hostLeaseGeneration),
                state_volume_ref=str(host.spec.stateAttachment["sourceRef"]),
            )
        except Exception as exc:  # noqa: BLE001 - every host is reclaimed
            cleanup_errors.append(exc)
        host.ended_at = time.monotonic() - origin
    result.cleanup_seconds = time.monotonic() - cleanup_started

    failures = [item for item in outcomes if isinstance(item, BaseException)]
    if failures:
        # A broken barrier is what the *peers* saw; the host that could not
        # launch or register is why the wave failed. Report the cause rather
        # than the consequence, or the row's diagnostic names the barrier and
        # not the host that never registered.
        raise next(
            (
                failure
                for failure in failures
                if not isinstance(failure, asyncio.BrokenBarrierError)
            ),
            failures[0],
        )
    if cleanup_errors:
        raise cleanup_errors[0]

    # Every execution received its own container, container id, state volume,
    # and addressable Omnigent host id — and the server agreed about the last
    # one, which is what makes it an execution identity rather than a label.
    for key in ("containerName", "containerId", "stateVolumeRef"):
        values = [str(host.launch[key]) for host in hosts]
        assert len(set(values)) == level, f"{key} was shared between exact hosts"
    expected_ids = {str(host.spec.expectedOmnigentHostId) for host in hosts}
    assert len(expected_ids) == level, (
        "two exact hosts shared one addressable Omnigent host id"
    )
    registered_ids = {str(host.registration["omnigentHostId"]) for host in hosts}
    assert len(registered_ids) == level, (
        "two exact hosts registered under one Omnigent host id"
    )

    scan, scan_errors = await _cleanup_scan(backend, hosts)
    result.scan = scan
    result.diagnostics = scan_errors
    result.samples = tuple(
        ExecutionOverlapSample(
            execution_ref=f"w{wave_index}-{host.run_ref}",
            started_at=host.started_at,
            ended_at=max(host.started_at, host.ended_at),
        )
        for host in hosts
    )
    return result


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
    host_server_url = _host_server_url()
    host_class = _host_class(image_ref)
    launch_policy = get_launch_policy(LAUNCH_POLICY_REF)
    backend = DockerCommandBackend()
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url=host_server_url,
        host_api_token=resolved_host_runner_token(),
    )
    client = OmnigentHttpClient(
        base_url=resolved_server_url(), api_token=resolved_api_token()
    )
    registration = OmnigentHostRegistrationService(
        client=client, expected_owner=_expected_host_owner(), backend=backend
    )
    cleanup_service = DockerOmnigentHostCleanupService(backend)

    # Pull once, outside the measured timeline. The repeated-wave budget is a
    # control-plane budget: charging the first wave for a cold registry would
    # measure the network, and would make the second wave incomparable to it.
    code, _out, err = await backend.run(["docker", "pull", image_ref], check=False)
    if code != 0:
        pytest.fail(f"the exact host image could not be pulled: {err.strip()[:512]}")

    origin = time.monotonic()
    waves: list[_WaveResult] = []
    for wave_index in range(EXACT_DOCKER_WAVES):
        waves.append(
            await _run_wave(
                wave_index=wave_index,
                level=level,
                origin=origin,
                tmp_path=tmp_path / f"wave-{wave_index}",
                image_ref=image_ref,
                host_server_url=host_server_url,
                host_class=host_class,
                launch_policy=launch_policy,
                backend=backend,
                launcher=launcher,
                registration=registration,
                cleanup_service=cleanup_service,
            )
        )

    for wave_index, wave in enumerate(waves):
        assert not wave.diagnostics, (
            f"wave {wave_index}: the teardown scan itself failed, so nothing "
            f"was proven about these resources: {wave.diagnostics}"
        )
        assert wave.scan.zero_leak, [
            entry.resource_ref for entry in wave.scan.unresolved
        ]

    # Bounded growth across repeated waves at the same level, against the
    # exact-image budget for this machine class. A single wave cannot see
    # growth that only appears after teardown and relaunch.
    report = RepeatedWaveReport(
        level=level,
        thresholds=EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS,
        waves=tuple(
            WaveObservation(
                wave_index=wave_index,
                observed_peak=wave.observed_peak,
                wait_seconds=wave.wait_seconds,
                launch_seconds=wave.launch_seconds,
                registration_seconds=wave.registration_seconds,
                control_seconds=wave.control_seconds,
                cleanup_seconds=wave.cleanup_seconds,
                # One lease generation and one registration per execution:
                # the per-execution control-plane work this layer must not grow
                # wave over wave.
                lease_mutations=len(wave.hosts),
                registration_requests=wave.registration_requests,
                transport_pool_peak=len(wave.hosts),
                residual_resources=len(wave.scan.unresolved),
            )
            for wave_index, wave in enumerate(waves)
        ),
    )
    assert report.bounded, report.violations

    overlap = ObservedOverlapEvidence(
        requested_level=level,
        effective_limit=level,
        barrier_synchronized=True,
        samples=tuple(
            sample for wave in waves for sample in wave.samples
        ),
    )
    assert overlap.observed_peak == level

    publish_observed_overlap(ConcurrencyQualificationLayer.exact_docker, overlap)


async def _credential_recovery_host_class(backend, image_ref):
    """Bind this recovery row to observed build/version and canonical harness identity."""
    import json

    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness

    _, build_digest, _ = await backend.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "moonmind.omnigent.build_digest"}}',
            image_ref,
        ]
    )
    _, architecture, _ = await backend.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Os}}/{{.Architecture}}",
            image_ref,
        ]
    )
    # The server's picker catalog omits native wrappers. Observe the installed
    # registry in the exact host image instead; a deployment overlay cannot
    # establish that this image actually contains the selected harness.
    _, installed, _ = await backend.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/opt/venv/bin/python",
            image_ref,
            "-c",
            (
                "import json; from importlib.metadata import version; "
                "from omnigent.harness_plugins import valid_harnesses, harness_capabilities; "
                f"harness_id = {HARNESS_ID!r}; "
                "capabilities = harness_capabilities().get(harness_id); "
                "row = {'id': harness_id, 'capabilities': capabilities.as_dict()} "
                "if harness_id in valid_harnesses() and capabilities is not None else None; "
                "print(json.dumps({'version': version('omnigent'), 'harness': row}))"
            ),
        ]
    )
    observed = json.loads(installed)
    raw = observed["harness"]
    assert raw is not None, "installed recovery harness is unavailable"
    version = observed["version"].strip()
    harness = _normalize_harness(
        raw,
        omnigent_version=version,
        omnigent_build_digest=build_digest.strip(),
    )
    payload = _host_class(image_ref).model_dump(mode="json", by_alias=True)
    payload.update(
        omnigentVersion=version,
        omnigentBuildDigest=build_digest.strip(),
        architectures=[architecture.strip()],
    )
    payload["declaredHarnessImplementations"][0]["implementationRef"] = (
        harness.implementation.implementation_ref()
    )
    return HostClass.model_validate(payload)


def _credential_recovery_receipt(
    *, source_commit, image_ref, host_class, before_facts, after_facts, workspace_digest
):
    """Build the existing receipt shared by the real journey and validator tests."""
    return {
        "schemaVersion": 1,
        "sourceCommit": source_commit,
        "hostImageRef": image_ref,
        "omnigentVersion": host_class.omnigentVersion,
        "omnigentBuildDigest": host_class.omnigentBuildDigest,
        "harnessImplementationRef": host_class.declaredHarnessImplementations[
            0
        ].implementationRef,
        "before": before_facts,
        "after": after_facts,
        "containerReplaced": True,
        "sameHost": True,
        "sameSession": True,
        "sameBridge": True,
        "runnerReconnected": True,
        "inputReplayed": False,
        "workspaceDigest": workspace_digest,
    }


def _credential_recovery_container_facts(
    raw: str, *, expected_state_volume: str
) -> dict:
    """Project only inspected identities required by the qualification receipt."""
    import json

    observed = json.loads(raw)
    state = [
        mount
        for mount in observed["mounts"]
        if mount.get("Destination") == "/home/app/.omnigent"
    ]
    assert len(state) == 1 and state[0]["Type"] == "volume"
    assert state[0]["Name"] == expected_state_volume
    return {
        "containerId": observed["containerId"],
        "hostImageId": observed["hostImageId"],
        "stateVolume": state[0]["Name"],
    }


@pytest.mark.asyncio
async def test_exact_host_replacement_resumes_same_session_without_provider_input(
    tmp_path,
):
    """Qualify the credential-free Docker/session boundary of legacy recovery.

    Use the existing pinned-image launcher and actual upstream retry owner. No
    message or model request is submitted: this row proves runner restoration,
    state-volume retention, current workspace save, and stable bridge/session
    identity. The OAuth-specific launcher/cleanup-CAS paths have separate
    deterministic boundary tests; a missing real environment is not a pass.
    """
    import json
    from pathlib import Path

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import Base
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    reason = _exact_docker_environment_reason()
    if reason:
        pytest.skip(f"exact-image credential recovery unavailable: {reason}")
    run_ref = "credential-recovery-" + uuid.uuid4().hex[:12]
    workflow_id = "mm:test:" + run_ref
    step_id = workflow_id + ":run-1:preserve:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    skills = tmp_path / "skills"
    workspace.mkdir(parents=True)
    skills.mkdir()
    for command in (
        ["git", "init", "--initial-branch=main", str(workspace)],
        ["git", "-C", str(workspace), "config", "user.name", "Recovery Fixture"],
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "user.email",
            "fixture@example.invalid",
        ],
    ):
        await asyncio.to_thread(
            subprocess.run, command, check=True, capture_output=True
        )
    (workspace / "tracked.txt").write_text("baseline\n")
    await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(workspace), "add", "."], check=True
    )
    await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(workspace), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    (workspace / "tracked.txt").write_text("current dirty work\n")
    (workspace / "untracked.txt").write_text("current untracked work\n")
    # The exact image runs as its declared unprivileged identity.
    await asyncio.to_thread(
        subprocess.run, ["chmod", "-R", "a+rwX", str(tmp_path)], check=True
    )
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id,
            workflow_id,
            step_id,
            "repo",
        )
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=workflow_id,
        idempotencyKey=run_ref,
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
        },
        stepExecution={
            "workflowId": workflow_id,
            "runId": "run-1",
            "logicalStepId": "preserve",
            "executionOrdinal": 1,
            "stepExecutionId": step_id,
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    image_ref = _host_image()
    host_class = _host_class(image_ref)
    policy = get_launch_policy(LAUNCH_POLICY_REF)
    spec = _launch_spec(
        run_ref=run_ref,
        image_ref=image_ref,
        server_url=_host_server_url(),
        workspace_path=str(workspace),
        skills_path=str(skills),
        limits=dict(policy.limits),
        runtime=dict(host_class.runtime),
    )
    backend = DockerCommandBackend()
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url=_host_server_url(),
        host_api_token=resolved_host_runner_token(),
    )
    spec = spec.model_copy(
        update={
            "controlAttachment": launcher.control_attachment(spec.hostLeaseRef),
        }
    )
    client = OmnigentHttpClient(
        base_url=resolved_server_url(), api_token=resolved_api_token()
    )
    registration = OmnigentHostRegistrationService(
        client=client, expected_owner=_expected_host_owner(), backend=backend
    )
    cleanup = DockerOmnigentHostCleanupService(backend)
    runtime = OmnigentOAuthHostRuntime(client=client, workspace_root=tmp_path)
    publisher = OmnigentWorkspacePublicationService(
        tmp_path, artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path / "saved")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = None

    async def container_facts():
        _, raw, _ = await backend.run(
            [
                "docker",
                "inspect",
                "--format",
                '{"containerId":"{{.Id}}","hostImageId":"{{.Image}}","mounts":{{json .Mounts}}}',
                spec.correlationName,
            ]
        )
        return _credential_recovery_container_facts(
            raw, expected_state_volume=spec.stateAttachment["sourceRef"]
        )

    source_commit = os.getenv("MOONMIND_CREDENTIAL_RECOVERY_SOURCE_COMMIT", "").strip()
    if not source_commit:
        source_commit = (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
            )
        ).strip()
    assert len(source_commit) == 40 and all(
        c in "0123456789abcdef" for c in source_commit
    )
    try:
        await backend.run(["docker", "pull", image_ref])
        host_class = await _credential_recovery_host_class(backend, image_ref)
        agents = await client.list_agents()
        agent = next(
            (item for item in agents if item.get("name") == "opencode-native-ui"), None
        )
        assert agent is not None, "credential-free OpenCode stock agent is unavailable"
        await launcher.launch(
            spec=spec,
            host_class=host_class,
            launch_policy=policy,
            credential_handles=[],
        )
        await _await_running(backend, spec.correlationName)
        await registration.wait_for_registration(
            correlation_name=spec.correlationName,
            harness_id=HARNESS_ID,
            credentialless=True,
            expected_host_id=spec.expectedOmnigentHostId,
        )
        created = await client.create_session(
            {
                "agent_id": agent["id"],
                "host_type": "external",
                "host_id": spec.expectedOmnigentHostId,
                "workspace": "/workspaces/run",
                "harness_override": HARNESS_ID,
                "initial_items": [],
                "title": "MoonMind isolated credential recovery qualification",
            }
        )
        session_id = created["id"]
        receipt = {"omnigentSessionId": session_id}
        await runtime._resume_preserved_session(
            receipt, expected_host_id=spec.expectedOmnigentHostId
        )
        initial = await client.get_session(session_id)
        initial_items = [
            item.get("id")
            for item in initial.get("items", [])
            if item.get("type") == "message"
        ]
        store = OmnigentBridgeSessionStore(factory)
        bridge = await store.get_or_create(
            request=request,
            endpoint_ref="exact-docker-test",
            agent_id=agent["id"],
            agent_name=agent["name"],
            target_metadata={},
        )
        await store.attach_session(request.idempotency_key, session_id)
        saved_before = await publisher.save_request_workspace(request)
        before_facts = await container_facts()
        before_facts.update(
            hostId=initial["host_id"],
            sessionId=session_id,
            bridgeId=bridge.bridge_session_id,
            runnerId=initial["runner_id"],
            messageItemIds=initial_items,
        )
        assert before_facts["runnerId"] and not initial_items
        _, expected_image, _ = await backend.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_ref]
        )
        assert before_facts["hostImageId"] == expected_image.strip()
        # Remove only the disposable container. The same existing launch owner
        # reuses the state volume and every identity in the original spec.
        await backend.run(["docker", "stop", "--time", "20", spec.correlationName])
        saved_stopped = await publisher.save_request_workspace(request)
        assert saved_stopped["workspaceDigest"] == saved_before["workspaceDigest"]
        await backend.run(["docker", "rm", spec.correlationName])
        await launcher.launch(
            spec=spec,
            host_class=host_class,
            launch_policy=policy,
            credential_handles=[],
        )
        await _await_running(backend, spec.correlationName)
        await registration.wait_for_registration(
            correlation_name=spec.correlationName,
            harness_id=HARNESS_ID,
            credentialless=True,
            expected_host_id=spec.expectedOmnigentHostId,
        )
        after_facts = await container_facts()
        assert after_facts["containerId"] != before_facts["containerId"]
        assert after_facts["hostImageId"] == before_facts["hostImageId"]
        assert after_facts["stateVolume"] == before_facts["stateVolume"]
        await runtime._resume_preserved_session(
            receipt, expected_host_id=spec.expectedOmnigentHostId
        )
        resumed = await client.get_session(session_id)
        assert resumed["id"] == session_id
        assert resumed["host_id"] == spec.expectedOmnigentHostId
        assert resumed["runner_online"] is True
        assert resumed.get("runner_id") != initial.get("runner_id")
        assert [
            item.get("id")
            for item in resumed.get("items", [])
            if item.get("type") == "message"
        ] == initial_items
        await engine.dispose()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        restarted_store = OmnigentBridgeSessionStore(factory)
        recovered_bridge = await restarted_store.get_existing(request.idempotency_key)
        assert recovered_bridge.bridge_session_id == bridge.bridge_session_id
        assert recovered_bridge.omnigent_session_id == session_id
        after_facts.update(
            hostId=resumed["host_id"],
            sessionId=resumed["id"],
            bridgeId=recovered_bridge.bridge_session_id,
            runnerId=resumed["runner_id"],
            messageItemIds=[
                item.get("id")
                for item in resumed.get("items", [])
                if item.get("type") == "message"
            ],
        )
        assert after_facts["runnerId"]
        assert (workspace / "tracked.txt").read_text() == "current dirty work\n"
        assert (workspace / "untracked.txt").read_text() == "current untracked work\n"
        output_root = Path(
            os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR", str(tmp_path))
        )
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "credential-recovery-exact-docker.json").write_text(
            json.dumps(
                _credential_recovery_receipt(
                    source_commit=source_commit, image_ref=image_ref, host_class=host_class,
                    before_facts=before_facts, after_facts=after_facts,
                    workspace_digest=saved_stopped["workspaceDigest"],
                ),
                sort_keys=True,
            )
            + "\n"
        )
    finally:
        try:
            if session_id:
                await client.delete_session(session_id, delete_branch=False)
        finally:
            try:
                await cleanup.cleanup(
                    container_name=spec.correlationName,
                    host_lease_ref=spec.hostLeaseRef,
                    host_lease_generation=spec.hostLeaseGeneration,
                    state_volume_ref=spec.stateAttachment["sourceRef"],
                    control_volume_ref=(spec.controlAttachment or {}).get("sourceRef"),
                )
            finally:
                await engine.dispose()
