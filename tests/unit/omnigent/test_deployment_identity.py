from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent import deployment_identity


def _generic_plan_payload(server_build: str) -> SimpleNamespace:
    return SimpleNamespace(
        executionRealizerRef="generic-omnigent-host@1",
        supportIdentity=SimpleNamespace(omnigentServerBuildRef=server_build),
    )


def test_plan_deployment_identity_accepts_exact_server_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: digest,
    )

    deployment_identity.assert_plan_matches_deployed_server(
        _generic_plan_payload(digest)
    )


def test_plan_deployment_identity_rejects_stale_server_before_launch(
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
        deployment_identity.assert_plan_matches_deployed_server(
            _generic_plan_payload("sha256:" + "a" * 64)
        )


def test_non_generic_replay_does_not_consult_generic_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def probe() -> str:
        raise AssertionError("unexpected probe")

    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        probe,
    )

    deployment_identity.assert_plan_matches_deployed_server(
        SimpleNamespace(
            executionRealizerRef="codex-profile-bound@1",
            supportIdentity=None,
        )
    )
