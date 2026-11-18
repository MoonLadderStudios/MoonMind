"""Bounded idle usage probe container (MoonLadderStudios/MoonMind#5363)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.credential_materializers import ProfileCredentialHome
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services import usage_probe
from moonmind.omnigent.host_services.usage_probe import (
    UsageProbeRunner,
    probe_container_name,
)
from moonmind.provider_profiles.usage_collection import UsageProbePlan
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE

pytestmark = pytest.mark.asyncio

LAUNCH = {
    "hostImageRef": "ghcr.io/moonladder/omnigent-host@sha256:" + "b" * 64,
    "runtimeUid": 1000,
    "runtimeGid": 1000,
    "limits": {
        "cpuMillis": 500,
        "memoryMiB": 512,
        "processes": 64,
        "temporaryStorageMiB": 64,
        "timeoutSeconds": 60,
    },
}
HOME = ProfileCredentialHome(volume_ref="codex-home-1", target_path="/home/app/.codex")


class Backend:
    def __init__(self, listed: str = "") -> None:
        self.calls: list[list[str]] = []
        self.listed = listed

    async def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "ps"]:
            return 0, self.listed, ""
        return 0, '{"remaining": 10}', ""


@pytest.fixture(autouse=True)
def egress(monkeypatch):
    attest = AsyncMock()
    monkeypatch.setattr(usage_probe, "attest_docker_egress", attest)
    return attest


async def _run(backend, plan, *, exclusive=True):
    return await UsageProbeRunner(backend).run(
        lease_id="provider-account-usage:alpha:1",
        profile_id="alpha",
        credential_generation=3,
        launch=LAUNCH,
        home=HOME,
        plan=plan,
        exclusive_grant=exclusive,
    )


async def test_probe_mounts_the_enrolled_home_read_only_with_scoped_egress(
    egress,
) -> None:
    backend = Backend()

    code, stdout = await _run(backend, UsageProbePlan(argv=("codex", "usage")))

    assert (code, stdout) == (0, b'{"remaining": 10}')
    egress.assert_awaited_once()
    [argv] = backend.calls
    joined = " ".join(argv)
    assert "--rm" in argv
    assert "type=volume,src=codex-home-1,dst=/home/app/.codex,readonly" in argv
    assert argv[argv.index("--network") + 1] == OMNIGENT_EGRESS_PROFILE.network_ref
    assert "--read-only" in argv and "--cap-drop" in argv
    assert f"--name {probe_container_name('provider-account-usage:alpha:1')}" in joined
    assert "moonmind.credential_generation=3" in argv
    # The container ends itself inside the grant lifetime.
    image = argv.index(LAUNCH["hostImageRef"])
    assert argv[-6:] == ["timeout", "-s", "KILL", "60", "codex", "usage"]
    assert "-u" in argv[image:] and "OPENAI_API_KEY" in argv[image:]


async def test_native_recovery_mounts_writable_only_under_the_exclusive_grant() -> None:
    backend = Backend()
    plan = UsageProbePlan(argv=("codex", "refresh"), writes_credentials=True)

    await _run(backend, plan)
    assert "type=volume,src=codex-home-1,dst=/home/app/.codex" in backend.calls[0]

    with pytest.raises(HarnessPlatformError):
        await _run(Backend(), plan, exclusive=False)


async def test_unpinned_image_is_refused_before_docker_runs() -> None:
    backend = Backend()
    with pytest.raises(HarnessPlatformError):
        await UsageProbeRunner(backend).run(
            lease_id="lease",
            profile_id="alpha",
            credential_generation=1,
            launch={**LAUNCH, "hostImageRef": "omnigent-host:latest"},
            home=HOME,
            plan=UsageProbePlan(argv=("codex",)),
            exclusive_grant=True,
        )
    assert backend.calls == []


async def test_cleanup_proves_the_container_is_gone() -> None:
    name = probe_container_name("lease-1")
    assert await UsageProbeRunner(Backend()).cleanup("lease-1") == {
        "containerName": name,
        "removed": True,
    }
    with pytest.raises(HarnessPlatformError):
        await UsageProbeRunner(Backend(listed=f"{name}\n")).cleanup("lease-1")


async def test_probe_plan_is_bounded() -> None:
    with pytest.raises(ValueError):
        UsageProbePlan(argv=("codex",), timeout_seconds=600)
    with pytest.raises(ValueError):
        UsageProbePlan(argv=())
