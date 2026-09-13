"""Current Omnigent deployment identity at execution authority boundaries."""

from __future__ import annotations

import os
import re
from typing import Any

from moonmind.omnigent.compatibility import versions_compatible
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_IMAGE_REF = re.compile(r"^.+@sha256:([0-9a-f]{64})$")
_BUILD_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class OmnigentDeploymentIdentityConflict(ValueError):
    """Raised when a plan targets a different deployed runtime build."""


class OmnigentDeploymentNotReady(HarnessPlatformError):
    """Transient deployment discovery gap; the workflow owns bounded waiting."""


def resolve_deployed_server_build_digest() -> str:
    """Return the exact server build currently owned by this deployment."""

    explicit = str(os.getenv("OMNIGENT_BUILD_DIGEST") or "").strip()
    # This optional operator value pins host build provenance, never the
    # server image. A host pin cannot hide an incompatible server replacement.
    if explicit and not _BUILD_DIGEST.fullmatch(explicit):
        raise HarnessPlatformError(
            "OMNIGENT_BUILD_DIGEST must be an exact sha256 identity",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    image_ref = str(os.getenv("OMNIGENT_IMAGE_REF") or "").strip()
    if image_ref and not _IMAGE_REF.fullmatch(image_ref):
        raise HarnessPlatformError(
            "OMNIGENT_IMAGE_REF must be an exact immutable image reference",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
        if state and state.server_image_ref:
            match = _IMAGE_REF.fullmatch(str(state.server_image_ref).strip())
            if match:
                return f"sha256:{match.group(1)}"
    except Exception:
        # The resolved state is an optimization; the exact configured image
        # remains an independently verifiable deployment identity.
        pass
    image_ref = str(os.getenv("OMNIGENT_IMAGE_REF") or "").strip()
    match = _IMAGE_REF.fullmatch(image_ref)
    if match:
        return f"sha256:{match.group(1)}"
    raise OmnigentDeploymentNotReady(
        "Omnigent deployment identity is temporarily unavailable; waiting for "
        "the bootstrap reconciler to observe the running server",
        code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
    )


def _resolve_deployed_host_image_ref(harness_id: str) -> str | None:
    """Resolve the deployment image for a supported generic host harness."""

    from moonmind.omnigent.harness_platform.host_classes import (
        get_opencode_host_image_ref,
        get_pi_host_image_ref,
    )

    if harness_id == "opencode-native":
        return get_opencode_host_image_ref()
    if harness_id == "pi-native":
        return get_pi_host_image_ref()
    return None


async def assert_plan_matches_deployed_runtime(plan_payload: Any) -> None:
    """Validate server compatibility without replacing immutable host authority.

    The immutable catalog supplies the admitted major.minor independently of
    MoonMind releases. Previously persisted inline version evidence is retained.
    The pinned host remains launchable when the default host image advances.
    """

    if getattr(plan_payload, "executionRealizerRef", None) != (
        "generic-omnigent-host@1"
    ):
        return
    support_identity = getattr(plan_payload, "supportIdentity", None)
    planned = str(
        getattr(support_identity, "omnigentServerBuildRef", None) or ""
    ).strip()
    if not _BUILD_DIGEST.fullmatch(planned):
        raise OmnigentDeploymentIdentityConflict(
            "execution plan lacks exact Omnigent server build authority"
        )
    deployed = resolve_deployed_server_build_digest()
    version = getattr(plan_payload, "omnigentVersion", None)
    catalog_ref = getattr(plan_payload, "harnessCatalogRef", None)
    if planned != deployed:
        if version is None and catalog_ref:
            from sqlalchemy.exc import SQLAlchemyError
            from api_service.db.base import async_session_maker
            from moonmind.omnigent.harness_platform.catalog_service import (
                DbHarnessCatalogRepository,
            )

            try:
                catalog = await DbHarnessCatalogRepository(async_session_maker).load(
                    catalog_ref
                )
            except (SQLAlchemyError, OSError) as exc:
                raise OmnigentDeploymentNotReady(
                    "Waiting for the admitted Omnigent catalog store to recover",
                    code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
                ) from exc
            except ValueError as exc:
                raise OmnigentDeploymentIdentityConflict(
                    "execution plan catalog failed immutable evidence validation"
                ) from exc
            if catalog is None:
                raise OmnigentDeploymentNotReady(
                    "The admitted Omnigent catalog is temporarily unavailable; "
                    "restore the recorded catalog before retrying",
                    code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
                )
            snapshot = catalog.snapshot
            if (
                snapshot.catalogRef != catalog_ref
                or snapshot.endpointRef != plan_payload.endpointRef
                or snapshot.omnigentBuildDigest != planned
            ):
                raise OmnigentDeploymentIdentityConflict(
                    "execution plan catalog conflicts with admitted server authority"
                )
            version = snapshot.omnigentVersion
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
        observation = (
            state.details.get("opencodeHostCompatibility", {}) if state else {}
        )
        observed_version = observation.get("serverVersion")
        if version and (
            not observed_version or observation.get("serverBuildDigest") != deployed
        ):
            raise OmnigentDeploymentNotReady(
                "Waiting for Omnigent version evidence bound to the deployed server",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )
        if not versions_compatible(version, observed_version):
            raise OmnigentDeploymentIdentityConflict(
                "execution plan targets an Omnigent server build that is no longer "
                "deployed with compatible major.minor evidence; "
                "the deployment owner must restore a compatible server"
            )
    if version or catalog_ref:
        return
    harness_id = str(getattr(plan_payload, "harnessId", None) or "").strip()
    deployed_host = _resolve_deployed_host_image_ref(harness_id)
    if deployed_host is None:
        return
    planned_host = str(getattr(plan_payload, "hostImageRef", None) or "").strip()
    if not _IMAGE_REF.fullmatch(planned_host):
        raise OmnigentDeploymentIdentityConflict(
            "execution plan lacks exact host image authority"
        )
    if planned_host != deployed_host:
        raise OmnigentDeploymentIdentityConflict(
            "execution plan targets a host image that is no longer "
            "deployed; create a fresh execution to compile current runtime authority"
        )


__all__ = [
    "OmnigentDeploymentIdentityConflict",
    "assert_plan_matches_deployed_runtime",
    "resolve_deployed_server_build_digest",
]
