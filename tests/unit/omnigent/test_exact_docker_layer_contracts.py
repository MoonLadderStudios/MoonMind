"""Hermetic contracts of the exact-Docker owning test.

Source issue: MoonLadderStudios/MoonMind#3885 (exact-Docker layer).

The owning executor in ``tests/integration/omnigent`` needs a real daemon and
the digest-pinned images, so it never runs in required CI. Two of its
invariants are not about Docker at all, and both were failures that cost a
whole qualification job:

* a wave whose host fails must *release* its peers rather than leave them
  parked on a barrier until the job's own 120-minute timeout, and
* a teardown scan proves absence only from Docker's own "no such" answer; an
  unreachable daemon proves nothing and must not be recorded as a clean sweep.

They are exercised here against fakes so a regression fails in the unit shard
instead of stranding containers on the qualification runner.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from moonmind.omnigent.concurrency_qualification import (
    EXACT_DOCKER_REQUIRED_ENV,
    unsatisfied_exact_docker_environment,
)
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from tests.integration.omnigent import test_exact_docker_n_way_concurrency as layer

HOST_IMAGE = "ghcr.io/example/opencode-host@sha256:" + "d" * 64

#: How long the released-peers assertion waits before calling the wave hung.
#: Generous for a fake substrate, and far below the qualification job's own
#: timeout, which is what the regression consumed.
_DEADLOCK_TIMEOUT_SECONDS = 20.0


class _FakeBackend:
    """A daemon that answers the two questions the layer asks it."""

    def __init__(self, *, inspect_result: tuple[int, str, str] | None = None) -> None:
        self.inspect_result = inspect_result
        self.calls: list[list[str]] = []

    async def run(self, argv, *, check=False, timeout_seconds=None, input_bytes=None):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "container", "inspect"] and "{{.State.Status}}" in argv:
            return 0, "running\n", ""
        if self.inspect_result is not None:
            return self.inspect_result
        return 1, "", "Error: No such container: whatever"


def _spec(index: int):
    return layer._launch_spec(
        run_ref=f"fake-{index}",
        image_ref=HOST_IMAGE,
        server_url="https://omnigent.example",
        workspace_path=f"/tmp/workspace-{index}",
        skills_path=f"/tmp/skills-{index}",
        limits=dict(get_launch_policy(layer.LAUNCH_POLICY_REF).limits),
        runtime={"uid": 1000, "gid": 1000, "home": "/home/app"},
    )


async def _scan_one(backend, *, kind: str = "container"):
    return await layer._scan_entry(
        backend,
        ["docker", "container", "inspect", "--format", "{{.Id}}", "mm-host"],
        resource_ref="mm-host",
        kind=kind,
    )


@pytest.mark.asyncio
async def test_only_dockers_own_not_found_answer_proves_a_resource_is_gone() -> None:
    entry, error = await _scan_one(
        _FakeBackend(inspect_result=(1, "", "Error: No such container: mm-host"))
    )

    assert entry.resolved is True
    assert error == ""


@pytest.mark.asyncio
async def test_a_failed_scan_is_not_evidence_that_teardown_succeeded() -> None:
    """An unreachable daemon establishes nothing about the resource."""

    entry, error = await _scan_one(
        _FakeBackend(
            inspect_result=(
                1,
                "",
                "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            )
        )
    )

    assert entry.resolved is False
    assert "scan failed" in error


@pytest.mark.asyncio
async def test_a_resource_the_daemon_still_reports_is_unresolved() -> None:
    entry, error = await _scan_one(_FakeBackend(inspect_result=(0, "sha256:abc", "")))

    assert entry.resolved is False
    assert error == ""


@pytest.mark.asyncio
async def test_a_scan_that_failed_never_reports_a_zero_leak_sweep() -> None:
    """``zero_leak`` is derived, so an unproven teardown is visible in the row."""

    backend = _FakeBackend(
        inspect_result=(1, "", "error during connect: dial unix: connection refused")
    )
    hosts = [layer._ExactHost(f"fake-{index}", _spec(index)) for index in range(2)]

    report, errors = await layer._cleanup_scan(backend, hosts)

    assert report.zero_leak is False
    assert len(errors) == len(hosts) * 2


@pytest.mark.asyncio
async def test_a_failed_host_releases_the_peers_parked_on_its_barrier(
    tmp_path,
) -> None:
    """``asyncio.Barrier.abort`` is a coroutine, and discarding it deadlocks.

    Every peer waits at the barrier until the whole wave has registered. When
    one host cannot register, an un-awaited ``abort()`` leaves the coroutine
    unscheduled, the waiters blocked, ``gather`` pending, and the cleanup loop
    unreached: the job hangs until its own timeout and strands every container
    it created.
    """

    level = 3
    reclaimed: list[str] = []
    registered = 0

    async def _launch(*, spec, host_class, launch_policy, credential_handles):
        return {
            "containerName": str(spec.correlationName),
            "containerId": f"id-{spec.correlationName}",
            "stateVolumeRef": str(spec.stateAttachment["sourceRef"]),
        }

    async def _wait_for_registration(
        *, correlation_name, harness_id, credentialless, expected_host_id
    ):
        nonlocal registered
        registered += 1
        if registered == level:
            # The last host of the wave: its peers are already parked on the
            # barrier waiting for exactly this registration.
            raise RuntimeError("host never registered with the configured server")
        return {"omnigentHostId": expected_host_id}

    async def _cleanup(
        *, container_name, host_lease_ref, host_lease_generation, state_volume_ref
    ):
        reclaimed.append(container_name)

    backend = _FakeBackend()
    launcher = SimpleNamespace(launch=_launch)
    registration = SimpleNamespace(wait_for_registration=_wait_for_registration)
    cleanup_service = SimpleNamespace(cleanup=_cleanup)

    async def _run() -> None:
        await layer._run_wave(
            wave_index=0,
            level=level,
            origin=time.monotonic(),
            tmp_path=tmp_path,
            image_ref=HOST_IMAGE,
            host_server_url="https://omnigent.example",
            host_class=layer._host_class(HOST_IMAGE),
            launch_policy=get_launch_policy(layer.LAUNCH_POLICY_REF),
            backend=backend,
            launcher=launcher,
            registration=registration,
            cleanup_service=cleanup_service,
        )

    with pytest.raises(RuntimeError, match="never registered"):
        await asyncio.wait_for(_run(), timeout=_DEADLOCK_TIMEOUT_SECONDS)

    # The wave returned by failing, not by hanging, and every host it created
    # was still offered to its cleanup authority.
    assert len(reclaimed) == level


@pytest.mark.asyncio
async def test_the_observed_window_opens_at_registration_not_at_liveness(
    tmp_path,
) -> None:
    """A container that is merely up has attempted nothing.

    Without this, ``N`` hosts that start and then fail to register — or retry
    forever — publish a passing exact-image row for an N-way path that cannot
    actually run anything.
    """

    level = 2
    registration_delay = 0.2

    async def _launch(*, spec, host_class, launch_policy, credential_handles):
        return {
            "containerName": str(spec.correlationName),
            "containerId": f"id-{spec.correlationName}",
            "stateVolumeRef": str(spec.stateAttachment["sourceRef"]),
        }

    async def _wait_for_registration(
        *, correlation_name, harness_id, credentialless, expected_host_id
    ):
        await asyncio.sleep(registration_delay)
        return {"omnigentHostId": expected_host_id}

    async def _cleanup(**_kwargs):
        return None

    wave = await layer._run_wave(
        wave_index=0,
        level=level,
        origin=time.monotonic(),
        tmp_path=tmp_path,
        image_ref=HOST_IMAGE,
        host_server_url="https://omnigent.example",
        host_class=layer._host_class(HOST_IMAGE),
        launch_policy=get_launch_policy(layer.LAUNCH_POLICY_REF),
        backend=_FakeBackend(),
        launcher=SimpleNamespace(launch=_launch),
        registration=SimpleNamespace(wait_for_registration=_wait_for_registration),
        cleanup_service=SimpleNamespace(cleanup=_cleanup),
    )

    assert wave.observed_peak == level
    for sample in wave.samples:
        assert sample.started_at >= registration_delay
    # One registration per execution: the per-execution control-plane work the
    # repeated-wave budget holds flat.
    assert wave.registration_requests == level


@pytest.mark.asyncio
async def test_a_host_that_never_registers_fails_the_wave(tmp_path) -> None:
    """Registration is the authority handoff, so its failure is the row's."""

    async def _launch(*, spec, host_class, launch_policy, credential_handles):
        return {
            "containerName": str(spec.correlationName),
            "containerId": f"id-{spec.correlationName}",
            "stateVolumeRef": str(spec.stateAttachment["sourceRef"]),
        }

    async def _never_registers(**_kwargs):
        raise RuntimeError("exact launched Omnigent host did not register ready")

    async def _cleanup(**_kwargs):
        return None

    with pytest.raises(RuntimeError, match="did not register ready"):
        await asyncio.wait_for(
            layer._run_wave(
                wave_index=0,
                level=2,
                origin=time.monotonic(),
                tmp_path=tmp_path,
                image_ref=HOST_IMAGE,
                host_server_url="https://omnigent.example",
                host_class=layer._host_class(HOST_IMAGE),
                launch_policy=get_launch_policy(layer.LAUNCH_POLICY_REF),
                backend=_FakeBackend(),
                launcher=SimpleNamespace(launch=_launch),
                registration=SimpleNamespace(wait_for_registration=_never_registers),
                cleanup_service=SimpleNamespace(cleanup=_cleanup),
            ),
            timeout=_DEADLOCK_TIMEOUT_SECONDS,
        )


def test_the_layer_declares_the_registration_authoritys_own_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precondition, the job and the owning test read one contract.

    A layer admitted without the control-plane endpoint, its token or the
    expected owner would enter, fail inside the registration authority, and
    record a ``failed`` concurrency row for what was really a missing variable.
    """

    assert set(EXACT_DOCKER_REQUIRED_ENV) >= {
        "OMNIGENT_SERVER_URL",
        "OMNIGENT_API_TOKEN",
        "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER",
    }
    assert unsatisfied_exact_docker_environment({}) == EXACT_DOCKER_REQUIRED_ENV
    satisfied = {name: "value" for name in EXACT_DOCKER_REQUIRED_ENV}
    assert unsatisfied_exact_docker_environment(satisfied) == ()

    # The owning test skips naming exactly those variables, so the runner's
    # ``unavailable`` row and the skip reason describe the same absence.
    for name in EXACT_DOCKER_REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    reason = layer._exact_docker_environment_reason()
    for name in EXACT_DOCKER_REQUIRED_ENV:
        assert name in reason
