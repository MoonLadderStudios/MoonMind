"""Pinned OpenCode runtime model-catalog validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.opencode_runtime_validation import (
    OpenCodeProviderRuntimeValidationService,
    _model_probe_argv,
    _validated_models,
)
from moonmind.security.egress import (
    OMNIGENT_EGRESS_NETWORK_REF,
    omnigent_proxy_env,
)


def test_model_probe_stages_auth_in_writable_home_with_restricted_egress() -> None:
    image_ref = "registry.example/opencode@sha256:" + "a" * 64
    argv = _model_probe_argv(
        image_ref=image_ref,
        credential_source="credential-volume",
        credential_target="/run/mm-credentials/opencode",
    )

    assert argv[:5] == [
        "docker",
        "run",
        "--rm",
        "--network",
        OMNIGENT_EGRESS_NETWORK_REF,
    ]
    assert "/home/app:rw,uid=1000,gid=1000,mode=0700" in argv
    assert (
        "type=volume,src=credential-volume," "dst=/run/mm-credentials/opencode,readonly"
    ) in argv
    for item in omnigent_proxy_env():
        assert item in argv
    script = argv[argv.index("-ceu") + 1]
    assert 'cp "$1/auth.json" /home/app/.local/share/opencode/auth.json' in script
    assert "exec opencode models --refresh" in script
    assert "head" not in script


def test_credentialless_model_probe_omits_auth_mount_and_staging() -> None:
    image_ref = "registry.example/opencode@sha256:" + "a" * 64
    argv = _model_probe_argv(image_ref=image_ref)

    assert "--mount" not in argv
    script = argv[argv.index("-ceu") + 1]
    assert "auth.json" not in script
    assert "exec opencode models --refresh" in script


@pytest.mark.asyncio
async def test_credentialless_zen_validation_never_resolves_or_mounts_a_secret() -> (
    None
):
    class _Backend:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        async def run(self, argv, **_kwargs):
            self.commands.append(list(argv))
            if "models --refresh" in " ".join(argv):
                return (
                    0,
                    b"opencode/muse-spark-1.2-contributor-free\n",
                    b"",
                )
            return 0, b"1.18.11\n", b""

    class _Artifacts:
        async def write_json(self, **_kwargs):
            return "artifact:credential-attestation"

    backend = _Backend()
    service = OpenCodeProviderRuntimeValidationService(
        session_factory=object(),
        resolver=None,
        image_ref="registry.example/opencode@sha256:" + "a" * 64,
        backend=backend,
        artifact_gateway=_Artifacts(),
    )
    evidence = await service.validate(
        profile=SimpleNamespace(
            profile_id="opencode-zen-free",
            runtime_id="opencode",
            provider_id="opencode",
            credential_generation=1,
            capacity_scope_ref=None,
        ),
        lease=SimpleNamespace(lease_id="lease-1"),
    )

    assert evidence["materializerRef"] == "none@1"
    model_probe = next(argv for argv in backend.commands if "-ceu" in argv)
    assert "--mount" not in model_probe


def test_validated_models_returns_only_observed_opencode_models() -> None:
    assert _validated_models(
        "opencode-go/gpt-5.6-luna\n"
        "opencode/muse-spark-1.2-contributor-free\n"
        "other-provider/model\n"
    ) == [
        "opencode-go/gpt-5.6-luna",
        "opencode/muse-spark-1.2-contributor-free",
    ]


@pytest.mark.parametrize("catalog", ["", [], {}, "other-provider/model"])
def test_validated_models_fail_closed_without_provider_evidence(catalog) -> None:
    with pytest.raises(HarnessPlatformError) as exc:
        _validated_models(catalog)

    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
