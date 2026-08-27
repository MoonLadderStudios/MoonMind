"""Pinned OpenCode runtime model-catalog validation."""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.opencode_runtime_validation import (
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


def test_validated_models_returns_only_observed_opencode_go_models() -> None:
    assert _validated_models("opencode-go/gpt-5.6-luna\nother-provider/model\n") == [
        "opencode-go/gpt-5.6-luna"
    ]


@pytest.mark.parametrize("catalog", ["", [], {}, "other-provider/model"])
def test_validated_models_fail_closed_without_provider_evidence(catalog) -> None:
    with pytest.raises(HarnessPlatformError) as exc:
        _validated_models(catalog)

    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
