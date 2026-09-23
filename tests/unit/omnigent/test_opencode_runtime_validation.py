"""Pinned OpenCode runtime model-catalog validation."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.opencode_runtime_validation import (
    CREDENTIAL_REJECTED_MARKER,
    OpenCodeProviderRuntimeValidationService,
    _model_probe_argv,
    _validated_models,
    is_confirmed_credential_rejection,
    is_transient_validation_error,
)
from moonmind.security.egress import OMNIGENT_EGRESS_NETWORK_REF, omnigent_proxy_env


REQUESTED_IMAGE = "registry.example/opencode@sha256:" + "a" * 64
EFFECTIVE_IMAGE = "registry.example/opencode@sha256:" + "b" * 64


def test_model_probe_stages_auth_in_writable_home_with_restricted_egress() -> None:
    image_ref = "registry.example/opencode@sha256:" + "a" * 64
    argv = _model_probe_argv(
        image_ref=image_ref,
        provider_id="opencode-go",
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
    argv = _model_probe_argv(image_ref=image_ref, provider_id="opencode")

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


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["openrouter", "vendor.v2_test"])
@pytest.mark.parametrize("probe_result", ["accepted", "rejected", "sibling-only"])
async def test_generic_validation_preserves_route_and_cleans_up(
    provider_id, probe_result
) -> None:
    class _Backend:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        async def run(self, argv, **kwargs):
            self.commands.append(list(argv))
            if kwargs.get("input_bytes"):
                import json

                assert json.loads(kwargs["input_bytes"]) == {
                    provider_id: {"type": "api", "key": "candidate-key"}
                }
            if argv[1:3] == ["volume", "ls"]:
                return 0, b"owned\n", b""
            if "models --refresh" in " ".join(argv):
                if probe_result == "rejected":
                    return 1, b"", b"invalid api key"
                catalog = f"{provider_id}-other/model\nother/model\n"
                if probe_result == "accepted":
                    catalog += f"{provider_id}/author/model:free\n"
                return 0, catalog.encode(), b""
            return 0, b"1.18.11\n", b""

    class _Artifacts:
        async def write_json(self, **kwargs):
            assert kwargs["payload"]["providerRouteRef"] == provider_id
            assert "candidate-key" not in str(kwargs)
            return "artifact:credential-attestation"

    backend = _Backend()
    service = OpenCodeProviderRuntimeValidationService(
        session_factory=object(),
        resolver=None,
        image_ref="registry.example/opencode@sha256:" + "a" * 64,
        backend=backend,
        artifact_gateway=_Artifacts(),
    )
    validation = service.validate(
        profile=SimpleNamespace(
            profile_id="custom",
            runtime_id="opencode",
            provider_id=provider_id,
            credential_generation=2,
            capacity_scope_ref=None,
        ),
        lease=SimpleNamespace(lease_id="lease-1"),
        candidate_secret="candidate-key",
        candidate_generation=2,
    )
    if probe_result == "accepted":
        evidence = await validation
        assert evidence["models"] == [
            {"qualifiedId": f"{provider_id}/author/model:free"}
        ]
        assert evidence["materializerRef"] == "opencode-auth-json@1"
        assert evidence["credentialGeneration"] == 2
        assert "candidate-key" not in str(evidence)
    else:
        with pytest.raises(HarnessPlatformError) as exc:
            await validation
        assert (
            exc.value.code
            == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
        )
    model_probe = next(
        argv for argv in backend.commands if "models --refresh" in " ".join(argv)
    )
    assert (
        model_probe[model_probe.index("--network") + 1] == OMNIGENT_EGRESS_NETWORK_REF
    )
    assert "readonly" in model_probe[model_probe.index("--mount") + 1]
    created = next(
        argv[-1] for argv in backend.commands if argv[1:3] == ["volume", "create"]
    )
    assert backend.commands[-1] == ["docker", "volume", "rm", created]
    assert "candidate-key" not in str(backend.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_id,provider_id",
    [("codex_cli", "openai"), ("claude_code", "anthropic"), ("omnigent", "openai")],
)
async def test_validation_rejects_unrelated_runtime_before_credential_resolution(
    runtime_id, provider_id
):
    from unittest.mock import AsyncMock

    backend = SimpleNamespace(
        run=AsyncMock(side_effect=AssertionError("unexpected Docker operation"))
    )
    service = OpenCodeProviderRuntimeValidationService(
        session_factory=object(),
        resolver=None,
        image_ref="registry.example/opencode@sha256:" + "a" * 64,
        backend=backend,
    )
    with pytest.raises(HarnessPlatformError) as exc:
        await service.validate(
            profile=SimpleNamespace(
                runtime_id=runtime_id,
                provider_id=provider_id,
                profile_id="other-runtime",
                credential_generation=1,
                capacity_scope_ref=None,
            ),
            lease=SimpleNamespace(lease_id="lease-1"),
            candidate_secret="test-key",
            candidate_generation=1,
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
    backend.run.assert_not_called()


@pytest.mark.parametrize(
    "provider", ["opencode-go", "opencode", "openrouter", "vendor.v2_test"]
)
def test_validated_models_returns_only_selected_provider(provider) -> None:
    assert _validated_models(
        {
            "models": [
                f"{provider}/author/model:free",
                f"{provider}-other/model",
                "other/model",
            ]
        },
        provider_id=provider,
    ) == [f"{provider}/author/model:free"]


@pytest.mark.parametrize("provider", ["openrouter", "opencode-go", "opencode"])
def test_sibling_catalog_does_not_validate_selected_provider(provider) -> None:
    with pytest.raises(HarnessPlatformError):
        _validated_models("opencode-other/model\nother/model", provider_id=provider)


@pytest.mark.parametrize("provider_id", ["opencode", "openrouter", "vendor.v2_test"])
@pytest.mark.parametrize("with_credentials", [False, True])
def test_model_probe_is_scoped_to_selected_provider(provider_id, with_credentials):
    argv = _model_probe_argv(
        image_ref="registry.example/opencode@sha256:" + "a" * 64,
        provider_id=provider_id,
        credential_source="credential-volume" if with_credentials else None,
        credential_target="/run/mm-credentials/opencode" if with_credentials else None,
    )
    script = argv[argv.index("-ceu") + 1]
    assert 'exec opencode models --refresh "$2"' in script
    assert argv[-3:] == [
        "--",
        "/run/mm-credentials/opencode" if with_credentials else "",
        provider_id,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["openrouter", "vendor.v2_test"])
@pytest.mark.parametrize("model_count", [2000, 40000])
async def test_catalog_capture_through_local_bounded_runner(
    monkeypatch, provider_id, model_count
):
    original_exec = asyncio.create_subprocess_exec
    commands = []
    payload = "".join(
        f"{provider_id}/author/model-{index:06d}:free\n" for index in range(model_count)
    ).encode()
    assert len(payload) > 16_384

    async def execute(*argv, **kwargs):
        commands.append(list(argv))
        if "models --refresh" in " ".join(argv):
            program = (
                "import sys; "
                f"sys.stdout.write(''.join(f'{provider_id}/author/model-{{i:06d}}:free\\n' "
                f"for i in range({model_count})))"
            )
        elif argv[1:3] == ("volume", "ls"):
            program = "print('owned')"
        elif kwargs.get("stdin") == asyncio.subprocess.PIPE:
            program = "import sys; sys.stdin.buffer.read()"
        else:
            program = "print('1.18.11')"
        return await original_exec(sys.executable, "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    artifacts = []

    class _Artifacts:
        async def write_json(self, **kwargs):
            artifacts.append(kwargs["payload"])
            return "artifact:credential-attestation"

    service = OpenCodeProviderRuntimeValidationService(
        session_factory=object(),
        resolver=None,
        image_ref="registry.example/opencode@sha256:" + "a" * 64,
        artifact_gateway=_Artifacts(),
    )
    validation = service.validate(
        profile=SimpleNamespace(
            profile_id="custom",
            runtime_id="opencode",
            provider_id=provider_id,
            credential_generation=1,
            capacity_scope_ref=None,
        ),
        lease=SimpleNamespace(lease_id="lease-1"),
        candidate_secret="candidate-key",
        candidate_generation=1,
    )
    if model_count == 2000:
        evidence = await validation
        assert evidence["models"] == [
            {"qualifiedId": item} for item in payload.decode().splitlines()
        ]
    else:
        with pytest.raises(HarnessPlatformError, match="output exceeded.*1048576"):
            await validation
        assert not any("--version" in argv for argv in commands)
        assert all("models" not in artifact for artifact in artifacts)
    assert commands[-1][0:3] == ["docker", "volume", "rm"]


@pytest.mark.parametrize("catalog", ["", [], {}, "other-provider/model"])
def test_validated_models_fail_closed_without_provider_evidence(catalog) -> None:
    with pytest.raises(HarnessPlatformError) as exc:
        _validated_models(catalog, provider_id="openrouter")

    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )


class _DriftBackend:
    """Fake Docker backend where requested image A is missing but B is installed."""

    def __init__(self, *, catalog_result=None) -> None:
        self.commands: list[list[str]] = []
        self.catalog_result = catalog_result

    async def run(self, argv, **kwargs):
        self.commands.append(list(argv))
        text = " ".join(argv)
        if argv[1:3] == ["image", "inspect"]:
            if REQUESTED_IMAGE in argv:
                return 1, b"", b"Error: No such image"
            if EFFECTIVE_IMAGE in argv:
                return 0, b"sha256:" + b"b" * 64 + b"\n", b""
            return 1, b"", b"Error: No such image"
        if argv[1:3] == ["volume", "ls"]:
            return 0, b"owned\n", b""
        if argv[1:3] == ["volume", "create"]:
            return 0, b"credential-volume\n", b""
        if "models --refresh" in text:
            if self.catalog_result is not None:
                return self.catalog_result
            return 0, b"opencode-go/author/model:free\n", b""
        return 0, b"1.18.11\n", b""


class _DriftArtifacts:
    async def write_json(self, **kwargs):
        return "artifact:credential-attestation"


def _drift_service(backend, monkeypatch) -> OpenCodeProviderRuntimeValidationService:
    monkeypatch.setattr(
        "moonmind.omnigent.opencode_runtime_validation."
        "compatible_deployed_fallback",
        lambda requested, **kwargs: (
            EFFECTIVE_IMAGE if requested == REQUESTED_IMAGE else None
        ),
    )
    return OpenCodeProviderRuntimeValidationService(
        session_factory=object(),
        resolver=None,
        image_ref=REQUESTED_IMAGE,
        backend=backend,
        artifact_gateway=_DriftArtifacts(),
    )


def _drift_profile():
    return SimpleNamespace(
        profile_id="drifted-profile",
        runtime_id="opencode",
        provider_id="opencode-go",
        credential_generation=1,
        capacity_scope_ref=None,
    )


@pytest.mark.asyncio
async def test_stale_image_uses_compatible_replacement_across_handoff(
    monkeypatch,
) -> None:
    """Original image A unavailable + trusted compatible B installed.

    Credential preparation, catalog discovery, and both version probes must
    all use B with no stale-A pull, and the evidence must truthfully name B.
    """

    backend = _DriftBackend()
    service = _drift_service(backend, monkeypatch)
    evidence = await service.validate(
        profile=_drift_profile(),
        lease=SimpleNamespace(lease_id="lease-1"),
        candidate_secret="candidate-key",
        candidate_generation=1,
    )

    assert evidence["imageRef"] == EFFECTIVE_IMAGE
    assert evidence["requestedImageRef"] == REQUESTED_IMAGE
    assert evidence["models"] == [
        {"qualifiedId": "opencode-go/author/model:free"}
    ]

    catalog_probes = [
        argv for argv in backend.commands if "models --refresh" in " ".join(argv)
    ]
    assert len(catalog_probes) == 1
    assert EFFECTIVE_IMAGE in catalog_probes[0]
    assert REQUESTED_IMAGE not in catalog_probes[0]

    version_probes = [argv for argv in backend.commands if "--version" in argv]
    assert len(version_probes) == 2
    for probe in version_probes:
        assert EFFECTIVE_IMAGE in probe
        assert REQUESTED_IMAGE not in probe

    writer_runs = [
        argv
        for argv in backend.commands
        if argv[:2] == ["docker", "run"] and "-ceu" in argv
    ]
    assert writer_runs
    assert any(EFFECTIVE_IMAGE in argv for argv in writer_runs)
    assert not any(REQUESTED_IMAGE in argv for argv in writer_runs)

    pulls = [argv for argv in backend.commands if argv[1:2] == ["pull"]]
    assert pulls == []


@pytest.mark.asyncio
async def test_installed_requested_image_keeps_original_evidence(
    monkeypatch,
) -> None:
    """No drift: evidence names the requested image with no extra keys."""

    class _PresentBackend(_DriftBackend):
        async def run(self, argv, **kwargs):
            self.commands.append(list(argv))
            text = " ".join(argv)
            if argv[1:3] == ["image", "inspect"]:
                return 0, b"sha256:" + b"a" * 64 + b"\n", b""
            if argv[1:3] == ["volume", "ls"]:
                return 0, b"owned\n", b""
            if argv[1:3] == ["volume", "create"]:
                return 0, b"credential-volume\n", b""
            if "models --refresh" in text:
                return 0, b"opencode-go/author/model:free\n", b""
            return 0, b"1.18.11\n", b""

    backend = _PresentBackend()
    service = _drift_service(backend, monkeypatch)
    evidence = await service.validate(
        profile=_drift_profile(),
        lease=SimpleNamespace(lease_id="lease-1"),
        candidate_secret="candidate-key",
        candidate_generation=1,
    )

    assert evidence["imageRef"] == REQUESTED_IMAGE
    assert "requestedImageRef" not in evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected_code", "must_contain"),
    [
        (
            b"error: invalid api key (401)",
            HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            CREDENTIAL_REJECTED_MARKER,
        ),
        (
            b"provider returned 403 unauthorized",
            HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            CREDENTIAL_REJECTED_MARKER,
        ),
        (
            b"dial tcp: connection refused",
            HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            "retry without changing the credential",
        ),
        (
            b"request timed out after 120s",
            HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            "retry without changing the credential",
        ),
        (
            b"exec: opencode: executable file not found in $PATH",
            HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            "does not support provider catalog discovery",
        ),
        (
            b"unknown command models for opencode",
            HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            "does not support provider catalog discovery",
        ),
        (
            b"unexpected catalog shape",
            HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            "unexpected catalog shape",
        ),
    ],
)
async def test_catalog_failure_classes_keep_stderr_and_disposition(
    monkeypatch, stderr, expected_code, must_contain
) -> None:
    backend = _DriftBackend(catalog_result=(1, b"", stderr))
    service = _drift_service(backend, monkeypatch)
    with pytest.raises(HarnessPlatformError) as exc:
        await service.validate(
            profile=_drift_profile(),
            lease=SimpleNamespace(lease_id="lease-1"),
            candidate_secret="candidate-key",
            candidate_generation=1,
        )

    assert exc.value.code == expected_code
    assert must_contain in str(exc.value)
    assert "(exit 1)" in str(exc.value)


@pytest.mark.parametrize(
    ("stderr", "rejected", "transient"),
    [
        (b"invalid api key", True, False),
        (b"connection refused", False, True),
        (b"unexpected catalog shape", False, False),
    ],
)
def test_failure_predicates_route_auth_invalid_and_budgets(
    monkeypatch, stderr, rejected, transient
) -> None:
    from moonmind.omnigent.opencode_runtime_validation import (
        _catalog_probe_failure,
    )

    err = _catalog_probe_failure(
        exit_code=1, stderr=stderr, effective_image_ref=EFFECTIVE_IMAGE
    )
    assert is_confirmed_credential_rejection(err) is rejected
    assert is_transient_validation_error(err) is transient


def test_infra_error_never_counts_as_credential_rejection() -> None:
    err = HarnessPlatformError(
        "boom", code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED
    )
    assert is_confirmed_credential_rejection(err) is False
    assert is_transient_validation_error(err) is True
