"""Current Omnigent deployment identity at execution authority boundaries."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


_IMAGE_REF = re.compile(r"^.+@sha256:([0-9a-f]{64})$")
_BUILD_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class OmnigentDeploymentIdentityConflict(ValueError):
    """Raised when a plan targets a different deployed server build."""


def resolve_deployed_server_build_digest() -> str:
    """Return the exact server build currently owned by this deployment."""

    explicit = str(os.getenv("OMNIGENT_BUILD_DIGEST") or "").strip()
    if explicit:
        if _BUILD_DIGEST.fullmatch(explicit):
            return explicit
        raise HarnessPlatformError(
            "OMNIGENT_BUILD_DIGEST must be an exact sha256 identity",
            code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
        )
    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
        if state and state.omnigent_build_digest:
            digest = str(state.omnigent_build_digest).strip()
            if _BUILD_DIGEST.fullmatch(digest):
                return digest
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
    try:
        output = subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "ghcr.io/omnigent-ai/omnigent-server:latest",
                "--format",
                "{{json .RepoDigests}}",
            ],
            text=True,
            timeout=5,
        )
        for value in json.loads(output.strip()):
            inspected = _IMAGE_REF.fullmatch(str(value))
            if inspected:
                return f"sha256:{inspected.group(1)}"
    except Exception:
        pass
    raise HarnessPlatformError(
        "OMNIGENT_IMAGE_REF must identify the exact Omnigent server digest",
        code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
    )


def assert_plan_matches_deployed_server(plan_payload: Any) -> None:
    """Reject a plan whose qualified server is no longer deployed.

    This check does not re-select or rewrite immutable plan authority. It
    verifies that the mutable endpoint the plan is about to call is still the
    exact server build included in the plan's support identity.
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
    if planned != resolve_deployed_server_build_digest():
        raise OmnigentDeploymentIdentityConflict(
            "execution plan targets an Omnigent server build that is no longer "
            "deployed; create a fresh execution to compile current runtime authority"
        )


__all__ = [
    "OmnigentDeploymentIdentityConflict",
    "assert_plan_matches_deployed_server",
    "resolve_deployed_server_build_digest",
]
