"""Shared Omnigent host image selection."""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.shared_host_image import (
    classify_effective_shared_host_image,
    resolve_effective_shared_host_image,
    resolve_shared_host_image_ref,
)

FAKE_DIGEST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "a1" * 32
LEGACY_DIGEST = "ghcr.io/omnigent-ai/omnigent-host" + "@sha256:" + "b2" * 32


def test_legacy_image_var_is_a_bounded_alias_only() -> None:
    assert (
        resolve_shared_host_image_ref({"OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST})
        == LEGACY_DIGEST
    )
    # The shared ref always wins; the alias never overrides it.
    assert (
        resolve_shared_host_image_ref(
            {
                "OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST,
                "OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST,
            }
        )
        == FAKE_DIGEST
    )
    assert (
        resolve_shared_host_image_ref({"OMNIGENT_HOST_IMAGE_REF": "some-image:latest"})
        == "some-image:latest"
    )
    assert resolve_shared_host_image_ref({}) == (
        "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"
    )


@pytest.mark.parametrize(
    "image_env, expected",
    [
        ({}, "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"),
        (
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": "registry.example/host:chosen"},
            "registry.example/host:chosen",
        ),
        (
            {"OMNIGENT_HOST_IMAGE_REF": "registry.example/legacy:chosen"},
            "registry.example/legacy:chosen",
        ),
        (
            {
                "OMNIGENT_SHARED_HOST_IMAGE": "registry.example/host",
                "OMNIGENT_SHARED_HOST_IMAGE_TAG": "chosen",
            },
            "registry.example/host:chosen",
        ),
    ],
)
def test_operator_image_is_selected_without_a_manual_pin(image_env, expected):
    assert resolve_effective_shared_host_image(image_env) == expected


@pytest.mark.parametrize(
    "ref",
    [
        FAKE_DIGEST,
        "host",
        "host:chosen",
        "registry.example/team/host",
        "registry.example/host:chosen",
        "localhost:5000/team/host",
        "registry.example:5000/team/host:Release_1.2-3",
        "registry.example:5000/team/host@sha256:" + "a1" * 32,
        "registry.example:5000/team/host:chosen@sha256:" + "a1" * 32,
    ],
)
def test_effective_image_preserves_shared_precedence(ref):
    env = {
        "OMNIGENT_SHARED_HOST_IMAGE_REF": ref,
        "OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST,
    }
    assert resolve_effective_shared_host_image(env) == ref
    assert resolve_shared_host_image_ref(env) == ref


@pytest.mark.parametrize(
    "env",
    [
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": "registry.example/host:"},
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": "not an image"},
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": "host@sha256:invalid"},
        {"OMNIGENT_HOST_IMAGE_REF": "registry.example/host/"},
        {"OMNIGENT_SHARED_HOST_IMAGE": "registry.example/host/"},
        {"OMNIGENT_SHARED_HOST_IMAGE_TAG": "chosen/"},
    ],
)
def test_effective_image_classifier_rejects_malformed_selection(env):
    image, supported, reason = classify_effective_shared_host_image(env)
    assert image is None
    assert supported is False
    assert "invalid shared host image reference" in reason
    with pytest.raises(HarnessPlatformError) as excinfo:
        resolve_effective_shared_host_image(env)
    assert excinfo.value.code == "OMNIGENT_HARNESS_BUILD_MISMATCH"
