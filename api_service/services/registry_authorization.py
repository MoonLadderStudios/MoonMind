"""API-owned private-image authorization for container jobs (MoonMind#3257).

The service authorizes, for every submission and run, that an authenticated
principal may execute a requested image and use a referenced registry credential
for the normalized registry/repository. It evaluates deployment-owned policy
only; it never resolves, reads, or returns credential values. Its output is a
bounded, non-sensitive :class:`RegistryAuthorization` that is safe to persist and
to cross Temporal workflow history.

Cache state is deliberately not an input: authorization is computed from the
principal, the requested image, the credential reference, and policy alone, so an
image already present in the shared daemon cannot bypass policy.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

_POLICY_ENV_VAR = "MOONMIND_REGISTRY_CREDENTIAL_GRANTS"

from moonmind.schemas.container_job_models import (
    ContainerJobFailureClass,
    ContainerJobSpec,
    NormalizedImageReference,
    OwnerIdentity,
    RegistryAuthorization,
    normalize_image_reference,
)


class RegistryCredentialGrant(BaseModel):
    """One deployment-owned grant binding a credential ref to a bounded scope."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    credential_ref: str = Field(alias="credentialRef", min_length=1, max_length=1024)
    registry: str = Field(min_length=1, max_length=255)
    repositories: tuple[str, ...] = Field(default_factory=tuple)
    principals: tuple[str, ...] = Field(default=("*",))
    tags: tuple[str, ...] = Field(default_factory=tuple)
    digests: tuple[str, ...] = Field(default_factory=tuple)

    def matches_principal(self, owner: OwnerIdentity) -> bool:
        if "*" in self.principals:
            return True
        token = f"{owner.principal_type}:{owner.principal_id}"
        return token in self.principals

    def repository_scope_for(self, repository: str) -> str | None:
        for pattern in self.repositories:
            if fnmatchcase(repository, pattern):
                return pattern
        return None

    def allows_tag(self, tag: str | None) -> bool:
        if not self.tags:
            return True
        return tag is not None and any(fnmatchcase(tag, pattern) for pattern in self.tags)

    def allows_digest(self, digest: str | None) -> bool:
        if not self.digests:
            return True
        return digest is not None and digest in self.digests


class PrivateImageAuthorizationPolicy(BaseModel):
    """Deployment-owned set of registry credential grants."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    grants: tuple[RegistryCredentialGrant, ...] = Field(default_factory=tuple)

    def grants_for(self, credential_ref: str) -> tuple[RegistryCredentialGrant, ...]:
        return tuple(
            grant for grant in self.grants if grant.credential_ref == credential_ref
        )

    def protects(self, reference: NormalizedImageReference) -> bool:
        """Return whether policy declares the image inside a private scope."""

        return any(
            grant.registry.lower() == reference.registry.lower()
            and grant.repository_scope_for(reference.repository) is not None
            for grant in self.grants
        )


class PrivateImageAuthorizationService:
    """Evaluate private-image execution and credential-use policy."""

    def __init__(self, policy: PrivateImageAuthorizationPolicy | None = None) -> None:
        self._policy = policy or PrivateImageAuthorizationPolicy()

    def authorize(
        self, *, owner: OwnerIdentity, spec: ContainerJobSpec
    ) -> RegistryAuthorization:
        if spec.image is None:
            raise ValueError(
                "registry authorization applies only to direct image references"
            )
        reference = normalize_image_reference(spec.image)
        credential_ref = spec.registry_credential_ref

        if credential_ref is None:
            if self._policy.protects(reference):
                return self._deny(
                    reference,
                    None,
                    ContainerJobFailureClass.IMAGE_USE_DENIED,
                    "a registry credential reference is required for this private image",
                )
            # A public image needs no registry credential. Execution policy for
            # public images is unrestricted in this deployment; a future policy
            # can deny here without changing the authorization contract.
            return RegistryAuthorization(
                authorized=True,
                registry=reference.registry,
                repository=reference.repository,
                reference=reference.reference,
            )

        return self._authorize_private(
            owner=owner, reference=reference, credential_ref=credential_ref
        )

    def _authorize_private(
        self,
        *,
        owner: OwnerIdentity,
        reference: NormalizedImageReference,
        credential_ref: str,
    ) -> RegistryAuthorization:
        grants = self._policy.grants_for(credential_ref)
        if not grants:
            return self._deny(
                reference,
                credential_ref,
                ContainerJobFailureClass.IMAGE_USE_DENIED,
                "no grant authorizes this credential reference",
            )

        # Track the most specific denial so the reported failure class reflects
        # how close the request came to an authorized grant.
        denial = self._deny(
            reference,
            credential_ref,
            ContainerJobFailureClass.IMAGE_USE_DENIED,
            "principal is not authorized to use this credential reference",
        )
        for grant in grants:
            if not grant.matches_principal(owner):
                continue
            if grant.registry.lower() != reference.registry.lower():
                denial = self._deny(
                    reference,
                    credential_ref,
                    ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                    "credential is not scoped to the requested registry",
                )
                continue
            scope = grant.repository_scope_for(reference.repository)
            if scope is None:
                denial = self._deny(
                    reference,
                    credential_ref,
                    ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                    "credential is not scoped to the requested repository",
                )
                continue
            if not grant.allows_tag(reference.tag) or not grant.allows_digest(
                reference.digest
            ):
                denial = self._deny(
                    reference,
                    credential_ref,
                    ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                    "requested tag or digest is outside the credential scope",
                )
                continue
            return RegistryAuthorization(
                authorized=True,
                registry=reference.registry,
                repository=reference.repository,
                reference=reference.reference,
                credentialRef=credential_ref,
                scope=scope,
                requiredDigest=reference.digest,
            )
        return denial

    @staticmethod
    def _deny(
        reference: NormalizedImageReference,
        credential_ref: str | None,
        failure_class: ContainerJobFailureClass,
        message: str,
    ) -> RegistryAuthorization:
        return RegistryAuthorization(
            authorized=False,
            registry=reference.registry,
            repository=reference.repository,
            reference=reference.reference,
            credentialRef=credential_ref,
            failureClass=failure_class,
            message=message,
        )


@functools.lru_cache(maxsize=1)
def load_default_authorization_policy() -> PrivateImageAuthorizationPolicy:
    """Load the deployment-owned grant policy from the environment.

    Grants are supplied as a JSON array in ``MOONMIND_REGISTRY_CREDENTIAL_GRANTS``.
    Absent or malformed configuration yields an empty (fail-closed) policy: jobs
    without a credential reference stay authorized, and any job that references a
    credential is denied until an operator declares a matching grant.
    """

    raw = str(os.environ.get(_POLICY_ENV_VAR, "")).strip()
    if not raw:
        return PrivateImageAuthorizationPolicy()
    try:
        grants = json.loads(raw)
        return PrivateImageAuthorizationPolicy.model_validate({"grants": grants})
    except (json.JSONDecodeError, ValidationError, TypeError):
        logger.warning(
            "Ignoring malformed %s; private-image credential use will fail closed.",
            _POLICY_ENV_VAR,
            exc_info=True,
        )
        return PrivateImageAuthorizationPolicy()


__all__ = [
    "PrivateImageAuthorizationPolicy",
    "PrivateImageAuthorizationService",
    "RegistryCredentialGrant",
    "load_default_authorization_policy",
]
