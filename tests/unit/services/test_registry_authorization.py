"""Private-image authorization service (MoonLadderStudios/MoonMind#3257)."""

from __future__ import annotations

import pytest

from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
    load_default_authorization_policy,
)
from moonmind.schemas.container_job_models import (
    ContainerJobFailureClass,
    ContainerJobSpec,
    OwnerIdentity,
)

USER_A = OwnerIdentity(principalId="user-a", principalType="user")
USER_B = OwnerIdentity(principalId="user-b", principalType="user")


def _spec(image: str, credential_ref: str | None = None) -> ContainerJobSpec:
    return ContainerJobSpec.model_validate(
        {
            "image": image,
            "workspaceRef": {"kind": "moonmind-session", "sessionId": "s"},
            "registryCredentialRef": credential_ref,
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        }
    )


def _service(**grant) -> PrivateImageAuthorizationService:
    policy = PrivateImageAuthorizationPolicy.model_validate({"grants": [grant]})
    return PrivateImageAuthorizationService(policy)


def test_public_image_needs_no_credential() -> None:
    service = PrivateImageAuthorizationService()
    result = service.authorize(owner=USER_A, spec=_spec("alpine"))
    assert result.authorized and result.credential_ref is None


def test_authorized_principal_gets_scope() -> None:
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["user:user-a"],
    )
    result = service.authorize(
        owner=USER_A, spec=_spec("ghcr.io/org/app:1", "db://ghcr")
    )
    assert result.authorized
    assert result.scope == "org/*"
    assert result.credential_ref == "db://ghcr"


def test_unauthorized_principal_is_denied_image_use() -> None:
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["user:user-a"],
    )
    result = service.authorize(
        owner=USER_B, spec=_spec("ghcr.io/org/app:1", "db://ghcr")
    )
    assert not result.authorized
    assert result.failure_class == ContainerJobFailureClass.IMAGE_USE_DENIED


def test_unknown_credential_ref_is_denied() -> None:
    service = _service(
        credentialRef="db://ghcr", registry="ghcr.io", repositories=["org/*"]
    )
    result = service.authorize(
        owner=USER_A, spec=_spec("ghcr.io/org/app:1", "db://other")
    )
    assert not result.authorized
    assert result.failure_class == ContainerJobFailureClass.IMAGE_USE_DENIED


def test_private_scope_requires_credential_even_for_cached_image() -> None:
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["user:user-a"],
    )
    result = service.authorize(owner=USER_A, spec=_spec("ghcr.io/org/app:1"))
    assert not result.authorized
    assert result.failure_class == ContainerJobFailureClass.IMAGE_USE_DENIED


def test_principal_grants_require_type_qualified_identity() -> None:
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["user-a"],
    )
    result = service.authorize(
        owner=USER_A, spec=_spec("ghcr.io/org/app:1", "db://ghcr")
    )
    assert not result.authorized


@pytest.mark.parametrize(
    "image",
    ["ghcr.io/other/app:1", "docker.io/org/app:1", "ghcr.io/org/app:blocked"],
)
def test_scope_mismatch_fails_closed(image) -> None:
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["*"],
        tags=["1", "2"],
    )
    result = service.authorize(owner=USER_A, spec=_spec(image, "db://ghcr"))
    assert not result.authorized
    assert result.failure_class == ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH


def test_digest_restriction_enforced() -> None:
    good = "sha256:" + "a" * 64
    service = _service(
        credentialRef="db://ghcr",
        registry="ghcr.io",
        repositories=["org/*"],
        principals=["*"],
        digests=[good],
    )
    other = "sha256:" + "b" * 64
    allowed = service.authorize(
        owner=USER_A, spec=_spec(f"ghcr.io/org/app@{good}", "db://ghcr")
    )
    assert allowed.authorized
    denied = service.authorize(
        owner=USER_A, spec=_spec(f"ghcr.io/org/app@{other}", "db://ghcr")
    )
    assert not denied.authorized
    assert denied.failure_class == ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH


def test_load_default_policy_from_env(monkeypatch) -> None:
    load_default_authorization_policy.cache_clear()
    monkeypatch.setenv(
        "MOONMIND_REGISTRY_CREDENTIAL_GRANTS",
        '[{"credentialRef": "db://ghcr", "registry": "ghcr.io", "repositories": ["org/*"]}]',
    )
    policy = load_default_authorization_policy()
    assert policy.grants_for("db://ghcr")
    monkeypatch.setenv("MOONMIND_REGISTRY_CREDENTIAL_GRANTS", "{not json")
    assert load_default_authorization_policy() is policy
    load_default_authorization_policy.cache_clear()
    assert load_default_authorization_policy().grants == ()
