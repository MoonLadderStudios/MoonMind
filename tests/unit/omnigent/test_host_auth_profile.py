"""Host-auth lifecycle coverage for MoonLadderStudios/MoonMind#3423."""

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_embedded import verify_embedded_host_auth
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    MAX_ROTATION_OVERLAP,
    load_host_auth_profile,
    profile_from_metadata,
    profile_persistence_metadata,
    resolve_host_auth_credentials,
    revoke_host_auth_profile,
    rotate_host_auth_profile,
)
from moonmind.omnigent.host_auth_adapter import PINNED_PROTOCOL_PROFILE


def _embedded_config():
    return parse_bridge_config(
        {
            "enabled": True,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "protocolProfile": PINNED_PROTOCOL_PROFILE,
                    "authMode": "upstream_runner_tunnel",
                    "proxyConformanceEvidenceRef": "artifact://proxy",
                    "liveSmokeEvidenceRef": "artifact://smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://auth",
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_current_and_bounded_previous_generations_resolve_without_durable_secrets(
    monkeypatch,
) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setenv("HOST_CURRENT", "current-token")
    monkeypatch.setenv("HOST_PREVIOUS", "previous-token")
    profile = HostAuthCredentialProfile(
        profile_id="managed-host-auth",
        current_secret_ref="env://HOST_CURRENT",
        current_generation=8,
        previous_secret_ref="env://HOST_PREVIOUS",
        previous_generation=7,
        rotated_at=now,
        previous_expires_at=now + MAX_ROTATION_OVERLAP,
    )

    resolved = await resolve_host_auth_credentials(profile=profile, now=now)
    context = verify_embedded_host_auth(
        headers={"X-Omnigent-Runner-Tunnel-Token": "previous-token"},
        config=_embedded_config(),
        configured_credentials=resolved.tokens_by_generation,
        credential_profile_id=profile.profile_id,
    )

    assert context.credential_generation == 7
    assert context.credential_profile_id == "managed-host-auth"
    assert "token" not in str(profile.metadata()).lower()


@pytest.mark.asyncio
async def test_expired_previous_generation_is_stale(monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setenv("HOST_CURRENT", "current-token")
    monkeypatch.setenv("HOST_PREVIOUS", "previous-token")
    profile = HostAuthCredentialProfile(
        profile_id="managed-host-auth",
        current_secret_ref="env://HOST_CURRENT",
        current_generation=8,
        previous_secret_ref="env://HOST_PREVIOUS",
        previous_generation=7,
        rotated_at=now - timedelta(minutes=10),
        previous_expires_at=now - timedelta(seconds=1),
    )

    resolved = await resolve_host_auth_credentials(profile=profile, now=now)
    assert resolved.tokens_by_generation == {8: "current-token"}


@pytest.mark.asyncio
async def test_overlapping_generations_cannot_resolve_to_same_token(monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setenv("HOST_CURRENT", "same-token")
    monkeypatch.setenv("HOST_PREVIOUS", "same-token")
    profile = HostAuthCredentialProfile(
        profile_id="managed-host-auth",
        current_secret_ref="env://HOST_CURRENT",
        current_generation=8,
        previous_secret_ref="env://HOST_PREVIOUS",
        previous_generation=7,
        rotated_at=now,
        previous_expires_at=now + timedelta(minutes=5),
    )

    with pytest.raises(HostAuthProfileError) as excinfo:
        await resolve_host_auth_credentials(profile=profile, now=now)

    assert excinfo.value.code == "host_auth_rotation_invalid"


def test_revocation_and_incompatible_profile_fail_without_secret_material() -> None:
    for profile, code in (
        (HostAuthCredentialProfile("managed", "env://HOST", 2, revoked=True), "host_auth_revoked"),
        (
            HostAuthCredentialProfile(
                "managed", "env://HOST", 2, protocol_profile="unsupported"
            ),
            "host_auth_profile_incompatible",
        ),
    ):
        with pytest.raises(HostAuthProfileError) as excinfo:
            profile.validate()
        assert excinfo.value.code == code
        assert "HOST" not in str(excinfo.value)


def test_invalid_rotation_overlap_fails_and_leaves_current_profile_unchanged() -> None:
    now = datetime.now(tz=UTC)
    profile = HostAuthCredentialProfile(
        profile_id="managed",
        current_secret_ref="env://CURRENT",
        current_generation=3,
        previous_secret_ref="env://PREVIOUS",
        previous_generation=2,
        rotated_at=now,
        previous_expires_at=now + MAX_ROTATION_OVERLAP + timedelta(seconds=1),
    )
    with pytest.raises(HostAuthProfileError, match="overlap"):
        profile.validate(now=now)
    assert profile.current_generation == 3


def test_environment_token_is_explicit_bootstrap_fallback() -> None:
    profile = load_host_auth_profile(env={"OMNIGENT_HOST_RUNNER_TOKEN": "local-only"})
    assert profile.bootstrap_fallback is True
    assert profile.current_secret_ref == "env://OMNIGENT_HOST_RUNNER_TOKEN"
    assert profile.protocol_profile == PINNED_PROTOCOL_PROFILE


def test_rotation_is_bounded_and_failure_does_not_mutate_current_profile() -> None:
    now = datetime.now(tz=UTC)
    current = HostAuthCredentialProfile("managed", "env://CURRENT", 4)
    rotated = rotate_host_auth_profile(
        current, new_secret_ref="env://NEXT", now=now, overlap=timedelta(minutes=5)
    )
    assert (rotated.current_generation, rotated.previous_generation) == (5, 4)
    assert current.current_generation == 4
    with pytest.raises(HostAuthProfileError) as excinfo:
        rotate_host_auth_profile(
            current,
            new_secret_ref="env://BAD",
            now=now,
            overlap=MAX_ROTATION_OVERLAP + timedelta(seconds=1),
        )
    assert excinfo.value.code == "host_auth_rotation_invalid"
    assert current.current_generation == 4


def test_revocation_removes_overlap_without_exposing_secret_refs() -> None:
    current = HostAuthCredentialProfile("managed", "env://CURRENT", 4)
    revoked = revoke_host_auth_profile(current)
    assert revoked.revoked is True
    assert revoked.previous_secret_ref is None
    assert "CURRENT" not in str(revoked.metadata())


def test_persistence_metadata_round_trip_contains_refs_but_never_secret_bodies() -> None:
    now = datetime.now(tz=UTC)
    profile = HostAuthCredentialProfile(
        "managed", "db://host-current", 4,
        previous_secret_ref="db://host-previous", previous_generation=3,
        rotated_at=now, previous_expires_at=now + timedelta(minutes=5),
    )
    metadata = profile_persistence_metadata(profile)
    encoded = str(metadata)
    assert "actual-current-secret" not in encoded
    assert "actual-previous-secret" not in encoded
    assert profile_from_metadata(metadata) == profile


@pytest.mark.asyncio
async def test_resolution_failure_does_not_leak_ref_or_secret(monkeypatch) -> None:
    secret = "credential-body-must-not-escape"
    monkeypatch.delenv("MISSING_HOST_AUTH", raising=False)
    profile = HostAuthCredentialProfile("managed", "env://MISSING_HOST_AUTH", 2)
    with pytest.raises(HostAuthProfileError) as excinfo:
        await resolve_host_auth_credentials(profile=profile)
    rendered = str(excinfo.value)
    assert secret not in rendered
    assert "MISSING_HOST_AUTH" not in rendered
    assert excinfo.value.code == "host_auth_secret_unavailable"
