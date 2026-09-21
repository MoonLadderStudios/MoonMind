from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent import deployment_identity


def _generic_plan_payload(server_build: str) -> SimpleNamespace:
    return SimpleNamespace(
        executionRealizerRef="generic-omnigent-host@1",
        supportIdentity=SimpleNamespace(omnigentServerBuildRef=server_build),
    )


def _opencode_plan_payload(
    server_build: str,
    host_image_ref: str,
) -> SimpleNamespace:
    payload = _generic_plan_payload(server_build)
    payload.harnessId = "opencode-native"
    payload.hostImageRef = host_image_ref
    return payload


def _pi_plan_payload(
    server_build: str,
    host_image_ref: str,
) -> SimpleNamespace:
    payload = _generic_plan_payload(server_build)
    payload.harnessId = "pi-native"
    payload.hostImageRef = host_image_ref
    return payload


@pytest.mark.asyncio
async def test_plan_deployment_identity_accepts_exact_server_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: digest,
    )

    await deployment_identity.assert_plan_matches_deployed_runtime(
        _generic_plan_payload(digest)
    )


@pytest.mark.asyncio
async def test_plan_deployment_identity_rejects_stale_server_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: "sha256:" + "b" * 64,
    )

    with pytest.raises(
        deployment_identity.OmnigentDeploymentIdentityConflict,
        match="no longer deployed",
    ):
        await deployment_identity.assert_plan_matches_deployed_runtime(
            _generic_plan_payload("sha256:" + "a" * 64)
        )


@pytest.mark.asyncio
async def test_plan_deployment_identity_allows_compatible_opencode_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-repository host digest drift launches on the installed target."""

    from moonmind.omnigent.harness_platform import host_classes

    server_digest = "sha256:" + "a" * 64
    current_host = "ghcr.io/example/opencode@sha256:" + "b" * 64
    rebuilt_host = "ghcr.io/example/opencode@sha256:" + "c" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: server_digest,
    )
    monkeypatch.setattr(
        host_classes,
        "get_opencode_host_image_ref",
        lambda: current_host,
    )

    await deployment_identity.assert_plan_matches_deployed_runtime(
        _opencode_plan_payload(server_digest, rebuilt_host)
    )


@pytest.mark.asyncio
async def test_plan_deployment_identity_rejects_foreign_opencode_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.harness_platform import host_classes

    server_digest = "sha256:" + "a" * 64
    current_host = "ghcr.io/example/opencode@sha256:" + "b" * 64
    foreign_host = "ghcr.io/example/other-host@sha256:" + "c" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: server_digest,
    )
    monkeypatch.setattr(
        host_classes,
        "get_opencode_host_image_ref",
        lambda: current_host,
    )

    with pytest.raises(
        deployment_identity.OmnigentDeploymentIdentityConflict,
        match="family",
    ):
        await deployment_identity.assert_plan_matches_deployed_runtime(
            _opencode_plan_payload(server_digest, foreign_host)
        )


@pytest.mark.asyncio
async def test_plan_deployment_identity_allows_compatible_pi_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-repository host digest drift launches on the installed target."""

    from moonmind.omnigent.harness_platform import host_classes

    server_digest = "sha256:" + "a" * 64
    current_host = "ghcr.io/example/pi@sha256:" + "b" * 64
    rebuilt_host = "ghcr.io/example/pi@sha256:" + "c" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: server_digest,
    )
    monkeypatch.setattr(host_classes, "get_pi_host_image_ref", lambda: current_host)

    await deployment_identity.assert_plan_matches_deployed_runtime(
        _pi_plan_payload(server_digest, rebuilt_host)
    )


@pytest.mark.asyncio
async def test_plan_deployment_identity_rejects_foreign_pi_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.harness_platform import host_classes

    server_digest = "sha256:" + "a" * 64
    current_host = "ghcr.io/example/pi@sha256:" + "b" * 64
    foreign_host = "ghcr.io/example/other-host@sha256:" + "c" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: server_digest,
    )
    monkeypatch.setattr(host_classes, "get_pi_host_image_ref", lambda: current_host)

    with pytest.raises(
        deployment_identity.OmnigentDeploymentIdentityConflict,
        match="family",
    ):
        await deployment_identity.assert_plan_matches_deployed_runtime(
            _pi_plan_payload(server_digest, foreign_host)
        )


@pytest.mark.asyncio
async def test_non_generic_replay_does_not_consult_generic_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def probe() -> str:
        raise AssertionError("unexpected probe")

    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        probe,
    )

    await deployment_identity.assert_plan_matches_deployed_runtime(
        SimpleNamespace(
            executionRealizerRef="codex-profile-bound@1",
            supportIdentity=None,
        )
    )
