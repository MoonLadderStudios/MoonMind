"""Tests for profile-owned OAuth credential-home materializers (issue #3829).

Covers ``codex-oauth-home@1`` and ``claude-oauth-home@1``:

- anticipated-handle requires an enrolled credential home
- target collision / wrong-layout rejection
- profile-owned read-write attachment with generation marker staging
- stale (newer) generation rejection
- idempotent same-generation re-materialization
- detach-only cleanup that preserves profile-owned state
- cleanup generation fencing and idempotent already-absent cleanup
- secret-free handles, evidence, and commands
"""

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.credential_materializers import (
    CredentialMaterializationContext,
    DockerClaudeOauthHomeMaterializer,
    DockerCodexOauthHomeMaterializer,
    ProfileCredentialHome,
    anticipated_credential_handle,
    build_default_credential_materializer_registry,
    credential_runtime_identity,
)
from moonmind.omnigent.harness_platform import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    validate_binding_materializer,
)

CODEX_REF = "codex-oauth-home@1"
CLAUDE_REF = "claude-oauth-home@1"

_FAKE_TOKEN = "super-secret-oauth-token-value"


class FakeDockerBackend:
    """Scripted Docker backend recording commands and returning canned output."""

    def __init__(self, *, volume_present: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.inputs: list[bytes | None] = []
        self.volume_present = volume_present
        self.writer_exit_code = 0

    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[int, bytes, bytes]:
        self.commands.append(list(argv))
        self.inputs.append(input_bytes)
        if argv[:2] == ["docker", "volume"] and argv[2] == "ls":
            if not self.volume_present:
                return 0, b"", b""
            return 0, b"present\n", b""
        if argv[:2] == ["docker", "run"]:
            if self.writer_exit_code != 0:
                return self.writer_exit_code, b"", b"simulated writer failure\n"
            return 0, b"", b""
        return 0, b"", b""


class FakeSecretBundle:
    def __init__(self) -> None:
        self.cleared = False

    def require(self, role: str) -> str:
        return _FAKE_TOKEN

    def clear(self) -> None:
        self.cleared = True


class FakeArtifactGateway:
    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []

    async def write_json(self, *, request, name, payload, link_type):
        self.written.append(
            {"name": name, "payload": payload, "link_type": link_type}
        )
        return f"artifact:{link_type}:{name}"


def _acquired(generation: int = 4) -> SimpleNamespace:
    return SimpleNamespace(
        provider_profile_ref="provider-profile-codex",
        provider_lease_ref="lease-1",
        credential_generation=generation,
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(idempotency_key="idem-1")


def _home(
    volume: str = "codex_auth_volume",
    target: str = "/home/app/.codex",
) -> ProfileCredentialHome:
    return ProfileCredentialHome(volume_ref=volume, target_path=target)


def _context(
    backend: FakeDockerBackend,
    *,
    home: ProfileCredentialHome | None = None,
    generation: int = 4,
) -> CredentialMaterializationContext:
    return CredentialMaterializationContext(
        request=_request(),
        acquired=_acquired(generation),
        secrets=FakeSecretBundle(),
        writer_image_ref="ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:"
        + "a" * 64,
        artifact_gateway=FakeArtifactGateway(),
        profile_credential_home=home,
    )


def _handle_json(handle) -> dict[str, Any]:
    return handle.model_dump(by_alias=True, mode="json")


# ---- Descriptor contract ----


@pytest.mark.parametrize("ref", (CODEX_REF, CLAUDE_REF))
def test_oauth_home_descriptors_are_profile_owned(ref):
    descriptor = get_materializer(ref)
    assert descriptor.state["scope"] == "profile"
    assert descriptor.state["mutable"] is True
    assert descriptor.cleanup["mode"] == "detach-profile-owned"
    assert descriptor.requiredSecretRoles == ()
    assert descriptor.acceptedAuthModels == ("oauth_volume",)


@pytest.mark.parametrize(
    ("ref", "harness_id", "target"),
    (
        (CODEX_REF, "codex-native", "/home/app/.codex"),
        (CLAUDE_REF, "claude-native", "/home/app/.claude"),
    ),
)
def test_oauth_home_binding_admission(ref, harness_id, target):
    descriptor = validate_binding_materializer(
        materializer_ref=ref,
        harness_implementation_ref="omnigent-harness-implementation:sha256:"
        + "a" * 64,
        harness_id=harness_id,
        host_mode="on-demand",
    )
    assert descriptor.target["path"] == target


def test_codex_materializer_rejects_claude_harness():
    with pytest.raises(HarnessPlatformError) as exc:
        validate_binding_materializer(
            materializer_ref=CODEX_REF,
            harness_implementation_ref="omnigent-harness-implementation:sha256:"
            + "a" * 64,
            harness_id="claude-native",
            host_mode="on-demand",
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE


# ---- Anticipated handles ----


def test_anticipated_handle_requires_enrolled_home():
    acquired = _acquired()
    with pytest.raises(HarnessPlatformError) as exc:
        anticipated_credential_handle(acquired, CODEX_REF)
    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
    )


def test_anticipated_handle_rejects_wrong_layout_target():
    acquired = _acquired()
    with pytest.raises(HarnessPlatformError) as exc:
        anticipated_credential_handle(
            acquired,
            CODEX_REF,
            profile_credential_home=_home(target="/home/app/.claude"),
        )
    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
    )


def test_anticipated_handle_rejects_unsafe_volume_name():
    acquired = _acquired()
    with pytest.raises(HarnessPlatformError):
        anticipated_credential_handle(
            acquired,
            CODEX_REF,
            profile_credential_home=_home(volume="bad volume;rm -rf /"),
        )


def test_anticipated_handle_is_read_write_profile_owned():
    acquired = _acquired()
    handle = anticipated_credential_handle(
        acquired, CLAUDE_REF, profile_credential_home=_home(volume="claude_auth_volume", target="/home/app/.claude")
    )
    attachment = handle.attachments[0]
    assert attachment.sourceRef == "claude_auth_volume"
    assert attachment.targetPath == "/home/app/.claude"
    assert attachment.accessMode == "read-write"
    assert handle.hostOwned is False
    # The handle is secret-free by construction.
    assert _FAKE_TOKEN not in _handle_json(handle)


# ---- Materialization ----


@pytest.mark.parametrize(
    ("ref", "cls", "volume", "target"),
    (
        (CODEX_REF, DockerCodexOauthHomeMaterializer, "codex_auth_volume", "/home/app/.codex"),
        (CLAUDE_REF, DockerClaudeOauthHomeMaterializer, "claude_auth_volume", "/home/app/.claude"),
    ),
)
@pytest.mark.asyncio
async def test_materialize_attaches_profile_owned_home_and_stages_generation(
    ref, cls, volume, target
):
    backend = FakeDockerBackend()
    impl = cls(backend)
    context = _context(backend, home=_home(volume=volume, target=target))
    handle = await impl.materialize(context)
    attachment = handle.attachments[0]
    assert attachment.model_dump(by_alias=True, mode="json") == {
        "kind": "volume",
        "sourceRef": volume,
        "targetPath": target,
        "accessMode": "read-write",
    }
    # The writer container is isolated: root identity only for staging, no
    # network, read-only root, writable profile-owned mount.
    writer_cmd = next(cmd for cmd in backend.commands if cmd[:2] == ["docker", "run"])
    assert "--user" in writer_cmd and "0:0" in writer_cmd
    assert "--network" in writer_cmd and "none" in writer_cmd
    assert "--read-only" in writer_cmd
    mount_index = writer_cmd.index("--mount")
    assert writer_cmd[mount_index + 1] == f"type=volume,src={volume},dst=/credential"
    # The generation marker command stages the acquired generation, never a
    # credential value.
    ceu = writer_cmd.index("-ceu")
    script = writer_cmd[ceu + 1]
    assert ".moonmind-generation" in script
    for command in backend.commands:
        joined = " ".join(command)
        assert _FAKE_TOKEN not in joined
    assert handle.attestationRef
    evidence = context.artifact_gateway.written[0]["payload"]
    assert evidence["ownership"] == "profile"
    assert evidence["secretCopied"] is False
    assert evidence["secretValueRecorded"] is False
    assert _FAKE_TOKEN not in str(evidence)
    # Secrets are released immediately: the enrollment volume is the state.
    assert context.secrets.cleared


@pytest.mark.asyncio
async def test_materialize_is_idempotent_for_the_same_generation():
    backend = FakeDockerBackend()
    impl = DockerCodexOauthHomeMaterializer(backend)
    first = await impl.materialize(_context(backend, home=_home()))
    second = await impl.materialize(_context(backend, home=_home()))
    assert first.credentialRuntimeRef == second.credentialRuntimeRef
    assert first.attachments == second.attachments


@pytest.mark.asyncio
async def test_materialize_requires_home():
    backend = FakeDockerBackend()
    impl = DockerCodexOauthHomeMaterializer(backend)
    with pytest.raises(HarnessPlatformError) as exc:
        await impl.materialize(_context(backend, home=None))
    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_materialize_rejects_missing_enrollment_volume():
    backend = FakeDockerBackend(volume_present=False)
    impl = DockerClaudeOauthHomeMaterializer(backend)
    with pytest.raises(HarnessPlatformError) as exc:
        await impl.materialize(_context(backend, home=_home(volume="claude_auth_volume", target="/home/app/.claude")))
    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
    )


@pytest.mark.asyncio
async def test_materialize_fails_when_writer_stale_generation_detected():
    """A newer enrollment generation must fence the stale lease before mount."""

    backend = FakeDockerBackend()
    backend.writer_exit_code = 79
    impl = DockerCodexOauthHomeMaterializer(backend)
    with pytest.raises(HarnessPlatformError) as exc:
        await impl.materialize(_context(backend, home=_home()))
    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
    )
    assert "generation" in str(exc.value)


# ---- Cleanup ----


@pytest.mark.asyncio
async def test_cleanup_preserves_profile_owned_state():
    backend = FakeDockerBackend()
    impl = DockerClaudeOauthHomeMaterializer(backend)
    handle = await impl.materialize(_context(backend, home=_home(volume="claude_auth_volume", target="/home/app/.claude")))
    result = await impl.cleanup(handle, handle.credentialGeneration)
    assert result.removed is False
    assert result.evidence["profileOwned"] is True
    assert result.evidence["preserved"] is True
    # No destructive Docker mutation happened.
    assert not any(
        cmd[:2] == ["docker", "volume"] and cmd[2] == "rm" for cmd in backend.commands
    )


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_when_home_already_absent():
    backend = FakeDockerBackend(volume_present=False)
    impl = DockerCodexOauthHomeMaterializer(backend)
    handle = anticipated_credential_handle(
        _acquired(), CODEX_REF, profile_credential_home=_home()
    )
    result = await impl.cleanup(handle, handle.credentialGeneration)
    assert result.removed is False
    assert result.evidence["alreadyAbsent"] is True


@pytest.mark.asyncio
async def test_cleanup_is_generation_fenced():
    backend = FakeDockerBackend()
    impl = DockerCodexOauthHomeMaterializer(backend)
    handle = anticipated_credential_handle(
        _acquired(generation=4), CODEX_REF, profile_credential_home=_home()
    )
    with pytest.raises(HarnessPlatformError) as exc:
        await impl.cleanup(handle, expected_generation=5)
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED


# ---- Registry wiring ----


def test_default_registry_includes_oauth_home_implementations():
    registry = build_default_credential_materializer_registry(
        backend=FakeDockerBackend()
    )
    assert registry.require(CODEX_REF).ref == CODEX_REF
    assert registry.require(CLAUDE_REF).ref == CLAUDE_REF


def test_runtime_identity_remains_generation_bound():
    acquired_a = _acquired(generation=4)
    acquired_b = _acquired(generation=5)
    ref_a, _ = credential_runtime_identity(acquired_a, CODEX_REF)
    ref_b, _ = credential_runtime_identity(acquired_b, CODEX_REF)
    assert ref_a != ref_b
