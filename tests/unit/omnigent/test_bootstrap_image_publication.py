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

    async def resolve(env=None) -> ResolvedOmnigentDeploymentState:
        del env
        return _state()

    monkeypatch.setattr(image_resolution, "resolve_omnigent_images", resolve)
    monkeypatch.setattr(store, "save_resolved_state", saved.append)
    image_resolution.reset_operator_image_configuration()
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
async def test_resolution_never_reads_a_digest_publication_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A published digest must not masquerade as an operator pin.

    ``_resolve_image`` short-circuits on an explicitly pinned ref, and the
    registry-acquiring policy leg keys on the same variables, so resolving
    against a self-published digest would permanently disable tag refresh for
    every configured mutable tag.
    """

    from moonmind.omnigent.bootstrap import store

    observed: list[str] = []

    async def resolve(env=None):
        source = env if env is not None else {}
        observed.append(str(source.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF") or ""))
        return _state()

    monkeypatch.setattr(image_resolution, "resolve_omnigent_images", resolve)
    monkeypatch.setattr(store, "save_resolved_state", lambda _state: None)
    image_resolution.reset_operator_image_configuration()
    monkeypatch.delenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", raising=False)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE", "ghcr.io/example/host")
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_TAG", "1.18.11")

    await image_resolution.publish_resolved_omnigent_images()
    # Publication exported the digest into the live environment...
    import os

    assert os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] == HOST_REF
    await image_resolution.publish_resolved_omnigent_images()

    # ...but the second pass still resolved from the unset operator baseline.
    assert observed == ["", ""]
    image_resolution.reset_operator_image_configuration()


def test_operator_pin_is_preserved_as_resolution_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit operator pin stays authoritative over the tag."""

    pinned = "ghcr.io/example/host@sha256:" + "7" * 64
    image_resolution.reset_operator_image_configuration()
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", pinned)

    configuration = image_resolution.operator_image_configuration()
    assert configuration["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] == pinned

    # Even after publication overwrites the live value, the baseline is the pin.
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", HOST_REF)
    assert (
        image_resolution.operator_image_configuration()[
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF"
        ]
        == pinned
    )
    image_resolution.reset_operator_image_configuration()


def test_selectors_fall_back_to_persisted_state_for_worker_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the API resolves images; every host-launching process selects one."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform import host_classes

    monkeypatch.delenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", raising=False)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)
    with pytest.raises(Exception):
        host_classes.get_opencode_host_image_ref()

    # Workers mount the same resolved-state file the API writes.
    monkeypatch.setattr(store, "load_resolved_state", lambda: _state())
    assert host_classes.get_opencode_host_image_ref() == HOST_REF

    # A placeholder digest in persisted state still fails closed.
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: _state(opencodeHostImageRef="ghcr.io/x/y@sha256:" + "0" * 64),
    )
    with pytest.raises(Exception):
        host_classes.get_opencode_host_image_ref()


@pytest.mark.asyncio
async def test_publication_never_clears_an_operator_pinned_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable optional image must not erase what the operator pinned."""

    from moonmind.omnigent.bootstrap import store

    async def resolve(env=None) -> ResolvedOmnigentDeploymentState:
        del env
        return _state(piHostImageRef=None)

    pinned_pi = "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:" + "4" * 64
    monkeypatch.setattr(image_resolution, "resolve_omnigent_images", resolve)
    monkeypatch.setattr(store, "save_resolved_state", lambda _state: None)
    image_resolution.reset_operator_image_configuration()
    monkeypatch.setenv("OMNIGENT_PI_HOST_IMAGE_REF", pinned_pi)

    await image_resolution.publish_resolved_omnigent_images()

    import os

    assert os.environ["OMNIGENT_PI_HOST_IMAGE_REF"] == pinned_pi
