"""Images the current Host Classes launch, as seen by storage maintenance."""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform import host_classes

SHARED = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "1" * 64
PI = "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:" + "2" * 64


@pytest.fixture(autouse=True)
def _no_persisted_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_classes, "_resolved_state_or_none", lambda: None)
    monkeypatch.setattr(host_classes, "_persisted_image_ref", lambda _key: "")


def test_lists_each_launchable_host_image_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex, Claude Code and OpenCode share one image; OpenCode's variable is
    # unset here, so only the images a launch could resolve are reported.
    monkeypatch.setattr(
        host_classes,
        "_installed_environment",
        lambda: {
            host_classes.OMNIGENT_SHARED_HOST_IMAGE_ENV: SHARED,
            host_classes.OMNIGENT_PI_HOST_IMAGE_ENV: PI,
        },
    )

    refs = host_classes.installed_host_class_image_refs()

    assert sorted(refs) == sorted((SHARED, PI))
    assert len(refs) == len(set(refs))


def test_unresolvable_images_are_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        host_classes,
        "_installed_environment",
        lambda: {host_classes.OMNIGENT_SHARED_HOST_IMAGE_ENV: "omnigent-host:latest"},
    )

    assert host_classes.installed_host_class_image_refs() == ()
