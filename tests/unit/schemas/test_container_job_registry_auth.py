"""Contract coverage for private-image authorization (MoonLadderStudios/MoonMind#3257)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobWorkflowInput,
    RegistryAuthorization,
    ensure_temporal_safe,
    failure_class_from_exception,
    normalize_image_reference,
)

JOB_ID = "container-job:" + "b" * 32
DIGEST = "sha256:" + "c" * 64


@pytest.mark.parametrize(
    ("image", "registry", "repository", "tag", "digest"),
    [
        ("alpine", "docker.io", "library/alpine", "latest", None),
        ("ubuntu:24.04", "docker.io", "library/ubuntu", "24.04", None),
        ("org/app:1.2", "docker.io", "org/app", "1.2", None),
        ("ghcr.io/org/app:1.2", "ghcr.io", "org/app", "1.2", None),
        (f"ghcr.io/org/app@{DIGEST}", "ghcr.io", "org/app", None, DIGEST),
        ("localhost:5000/team/svc:dev", "localhost:5000", "team/svc", "dev", None),
        ("registry.example.com:5000/a/b", "registry.example.com:5000", "a/b", "latest", None),
        ("index.docker.io/library/redis:7", "docker.io", "library/redis", "7", None),
    ],
)
def test_normalize_image_reference(image, registry, repository, tag, digest) -> None:
    ref = normalize_image_reference(image)
    assert (ref.registry, ref.repository, ref.tag, ref.digest) == (
        registry,
        repository,
        tag,
        digest,
    )


def test_normalize_rejects_empty_and_bad_digest() -> None:
    with pytest.raises(ValueError):
        normalize_image_reference("")
    with pytest.raises(ValueError):
        normalize_image_reference("ghcr.io/org/app@sha256:short")


def test_new_failure_classes_exist() -> None:
    values = {member.value for member in ContainerJobFailureClass}
    assert {
        "image_use_denied",
        "credential_unresolved",
        "repository_scope_mismatch",
        "registry_auth_failed",
        "credential_cleanup_failed",
    } <= values


def test_authorization_is_temporal_safe_and_non_sensitive() -> None:
    authorization = RegistryAuthorization(
        authorized=True,
        registry="ghcr.io",
        repository="org/app",
        reference="ghcr.io/org/app:1",
        credentialRef="db://ghcr-pull",
        scope="org/*",
    )
    request = ContainerJobActivityRequest(
        jobId=JOB_ID,
        ownershipToken="token",
        request={
            "idempotencyKey": "k",
            "source": {"source": "mcp"},
            "spec": {
                "image": "ghcr.io/org/app:1",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "s"},
                "registryCredentialRef": "db://ghcr-pull",
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            },
        },
        registryAuthorization=authorization,
    )
    # Crossing workflow history must succeed and carry only the reference/scope.
    encoded = ensure_temporal_safe(request)
    assert b"registryAuthorization" in encoded
    assert b"db://ghcr-pull" in encoded
    # No credential value or Docker auth blob leaks into history.
    assert b'"password"' not in encoded
    assert b'"auth"' not in encoded and b'"auths"' not in encoded


def test_authorization_model_rejects_raw_credential_key() -> None:
    # A credential *value* keyed as password/token/auth must never validate onto
    # the non-sensitive authorization contract.
    with pytest.raises(ValidationError):
        RegistryAuthorization.model_validate(
            {
                "authorized": True,
                "registry": "ghcr.io",
                "repository": "org/app",
                "reference": "ghcr.io/org/app:1",
                "password": "s3cret",
            }
        )


def test_workflow_input_accepts_optional_authorization_for_in_flight_compat() -> None:
    base = {
        "jobId": JOB_ID,
        "request": {
            "idempotencyKey": "k",
            "source": {"source": "workflow"},
            "spec": {
                "image": "alpine",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "s"},
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            },
        },
    }
    # Old payloads without the field remain valid (default None).
    legacy = ContainerJobWorkflowInput.model_validate(base)
    assert legacy.registry_authorization is None


def test_failure_class_survives_marker_and_wrapping() -> None:
    error = ContainerJobBackendError(
        ContainerJobFailureClass.REGISTRY_AUTH_FAILED, "denied by registry"
    )
    assert failure_class_from_exception(error) is (
        ContainerJobFailureClass.REGISTRY_AUTH_FAILED
    )
    wrapped = RuntimeError(f"Activity task failed: {error}")
    assert failure_class_from_exception(wrapped) is (
        ContainerJobFailureClass.REGISTRY_AUTH_FAILED
    )
    assert failure_class_from_exception(RuntimeError("unrelated")) is None
