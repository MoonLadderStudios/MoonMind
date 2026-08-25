"""Deployment image identities must reach every selector, not one request.

Host Class selection, launch policy compilation, and Provider Profile runtime
validation all read digest-pinned refs from the process environment. The
canonical Compose path leaves ``OMNIGENT_OPENCODE_HOST_IMAGE_REF`` unset so the
deployment resolves its own digests, so the resolution boundary has to export
them where those selectors look.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.omnigent.bootstrap import image_resolution
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState

SERVER_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
HOST_REF = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "2" * 64
BUILD_DIGEST = "sha256:" + "3" * 64


def _state(**overrides) -> ResolvedOmnigentDeploymentState:
    payload = {
        "serverImageRef": SERVER_REF,
        "opencodeHostImageRef": HOST_REF,
        "piHostImageRef": None,
        "omnigentBuildDigest": BUILD_DIGEST,
        "architecture": "linux/amd64",
        "resolvedAt": datetime(2026, 8, 25, tzinfo=UTC),
        "source": "auto",
    }
    payload.update(overrides)
    return ResolvedOmnigentDeploymentState.model_validate(payload)


@pytest.mark.asyncio
async def test_publication_exports_resolved_digests_and_persists_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.host_classes import (
        get_opencode_host_image_ref,
    )

    saved: list[ResolvedOmnigentDeploymentState] = []

    async def resolve() -> ResolvedOmnigentDeploymentState:
        return _state()

    monkeypatch.setattr(image_resolution, "resolve_omnigent_images", resolve)
    monkeypatch.setattr(store, "save_resolved_state", saved.append)
    # The default Compose path ships these unset.
    for key in (
        "OMNIGENT_IMAGE_REF",
        "OMNIGENT_BUILD_DIGEST",
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    ):
        monkeypatch.delenv(key, raising=False)

    published = await image_resolution.publish_resolved_omnigent_images()

    assert published.opencode_host_image_ref == HOST_REF
    assert [item.opencode_host_image_ref for item in saved] == [HOST_REF]
    # Host Class selection reads the digest straight from the environment, so
    # publication is what makes it selectable at all.
    assert get_opencode_host_image_ref() == HOST_REF


@pytest.mark.asyncio
async def test_publication_never_clears_an_operator_pinned_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable optional image must not erase what the operator pinned."""

    from moonmind.omnigent.bootstrap import store

    async def resolve() -> ResolvedOmnigentDeploymentState:
        return _state(piHostImageRef=None)

    pinned_pi = "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:" + "4" * 64
    monkeypatch.setattr(image_resolution, "resolve_omnigent_images", resolve)
    monkeypatch.setattr(store, "save_resolved_state", lambda _state: None)
    monkeypatch.setenv("OMNIGENT_PI_HOST_IMAGE_REF", pinned_pi)

    await image_resolution.publish_resolved_omnigent_images()

    import os

    assert os.environ["OMNIGENT_PI_HOST_IMAGE_REF"] == pinned_pi
