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
        "details": {
            "opencodeHostCompatibility": {
                "status": "ready",
                "failureCode": None,
                "serverImageRef": SERVER_REF,
                "hostImageRef": HOST_REF,
            }
        },
    }
    payload.update(overrides)
    return ResolvedOmnigentDeploymentState.model_validate(payload)


@pytest.mark.asyncio
async def test_mutable_image_resolution_refreshes_a_cached_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = "ghcr.io/example/host@sha256:" + "5" * 64
    cached = "ghcr.io/example/host@sha256:" + "6" * 64
    calls: list[str] = []

    async def pull(image, tag):
        calls.append(f"pull:{image}:{tag}")
        return refreshed

    async def inspect(image):
        calls.append(f"inspect:{image}")
        return cached

    monkeypatch.setattr(image_resolution, "_resolve_via_docker_pull", pull)
    monkeypatch.setattr(image_resolution, "_resolve_via_docker_inspect", inspect)

    ref, digest = await image_resolution._resolve_image(
        "IMAGE",
        "TAG",
        "REF",
        {"IMAGE": "ghcr.io/example/host", "TAG": "latest"},
    )

    assert ref == refreshed
    assert digest == "sha256:" + "5" * 64
    assert calls == ["pull:ghcr.io/example/host:latest"]


@pytest.mark.asyncio
async def test_compatibility_uses_the_running_compose_server_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry pull cannot change authority before Compose cuts over."""

    from moonmind.omnigent.bootstrap import store

    registry_server_ref = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "9" * 64
    resolved_inputs: list[str] = []

    async def running_server(image, env):
        assert image == "ghcr.io/omnigent-ai/omnigent-server"
        assert env["OMNIGENT_IMAGE_TAG"] == "latest"
        return SERVER_REF

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        resolved_inputs.append(image_env)
        if image_env == "OMNIGENT_IMAGE":
            raise AssertionError(
                f"must not resolve the registry server {registry_server_ref}"
            )
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "2" * 64
        return None, None

    async def build_identity(_image_ref):
        return "sha256:" + "1" * 64

    async def version(_image_ref):
        return "0.12.0"

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            return 0, "amd64", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(
        image_resolution, "_resolve_running_server_image", running_server
    )
    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_image_build_identity", build_identity)
    monkeypatch.setattr(image_resolution, "_image_omnigent_version", version)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images(
        {
            "OMNIGENT_IMAGE": "ghcr.io/omnigent-ai/omnigent-server",
            "OMNIGENT_IMAGE_TAG": "latest",
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": HOST_REF,
        }
    )

    assert resolved.server_image_ref == SERVER_REF
    assert resolved.details["serverImageDigest"] == "sha256:" + "1" * 64
    assert resolved.details["opencodeHostCompatibility"]["status"] == "ready"
    assert "OMNIGENT_IMAGE" not in resolved_inputs


@pytest.mark.asyncio
async def test_mutable_server_fails_closed_without_live_compose_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither a registry pull nor persisted state can impersonate Compose."""

    from moonmind.omnigent.bootstrap import store

    async def running_server(_image, _env):
        return None

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        if image_env == "OMNIGENT_IMAGE":
            raise AssertionError("mutable server registry state is not authority")
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "2" * 64
        return None, None

    async def build_identity(_image_ref):
        return BUILD_DIGEST

    async def version(_image_ref):
        return "0.12.0"

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            return 0, "amd64", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(
        image_resolution, "_resolve_running_server_image", running_server
    )
    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_image_build_identity", build_identity)
    monkeypatch.setattr(image_resolution, "_image_omnigent_version", version)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", _state)

    resolved = await image_resolution.resolve_omnigent_images(
        {
            "OMNIGENT_IMAGE": "ghcr.io/omnigent-ai/omnigent-server",
            "OMNIGENT_IMAGE_TAG": "latest",
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": HOST_REF,
        }
    )

    assert resolved.server_image_ref is None
    assert resolved.details["opencodeHostCompatibility"]["failureCode"] == (
        "omnigent_server_build_unavailable"
    )


@pytest.mark.asyncio
async def test_running_server_resolution_selects_the_compose_repository_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = "sha256:" + "8" * 64
    unrelated = "ghcr.io/example/unrelated@sha256:" + "7" * 64
    calls: list[list[str]] = []

    async def run(cmd, timeout=30):
        del timeout
        calls.append(cmd)
        if cmd[1] == "ps":
            return 0, "container-id\n", ""
        if cmd[1] == "inspect":
            return 0, image_id + "\n", ""
        if cmd[1:3] == ["image", "inspect"]:
            return 0, f'["{unrelated}", "{SERVER_REF}"]\n', ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_run", run)

    resolved = await image_resolution._resolve_running_server_image(
        "ghcr.io/omnigent-ai/omnigent-server",
        {"MOONMIND_DEPLOYMENT_PROJECT_NAME": "moonmind-test"},
    )

    assert resolved == SERVER_REF
    assert "label=com.docker.compose.project=moonmind-test" in calls[0]
    assert "label=com.docker.compose.service=omnigent" in calls[0]


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
    monkeypatch.setattr(store, "load_resolved_state", _state)
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
async def test_default_resolution_requires_paired_build_identity_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default tag path must attest the shared build identity exactly."""

    from moonmind.omnigent.bootstrap import store

    server_image_digest = BUILD_DIGEST
    host_image_digest = "sha256:" + "2" * 64

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, env
        if image_env == "OMNIGENT_IMAGE":
            return SERVER_REF, server_image_digest
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, host_image_digest
        if ref_env == "OMNIGENT_PI_HOST_IMAGE_REF":
            return None, None
        raise AssertionError(image_env)

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            if cmd[-1] == "{{json .Config.Labels}}":
                return (
                    0,
                    '{"moonmind.omnigent.build_digest":"' + BUILD_DIGEST + '"}',
                    "",
                )
            if cmd[-1] == "{{.Architecture}}":
                return 0, "amd64", ""
        if cmd[:5] == [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/venv/bin/omnigent",
        ]:
            return 0, "omnigent 0.12.0 (built for test)\n", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images({})

    assert resolved.server_image_ref == SERVER_REF
    assert resolved.opencode_host_image_ref == HOST_REF
    assert resolved.omnigent_build_digest == BUILD_DIGEST
    assert resolved.details == {
        "serverImageDigest": server_image_digest,
        "buildIdentitySource": "opencode-host-label",
        "opencodeHostCompatibility": {
            "status": "ready",
            "failureCode": None,
            "serverImageRef": SERVER_REF,
            "hostImageRef": HOST_REF,
            "serverBuildDigest": server_image_digest,
            "hostBuildDigest": BUILD_DIGEST,
            "serverVersion": "0.12.0",
            "hostVersion": "0.12.0",
        },
    }


@pytest.mark.asyncio
async def test_resolution_quarantines_operator_and_host_build_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.bootstrap import store

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        if image_env == "OMNIGENT_IMAGE":
            return SERVER_REF, BUILD_DIGEST
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "2" * 64
        return None, None

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            if cmd[-1] == "{{json .Config.Labels}}":
                return (
                    0,
                    '{"moonmind.omnigent.build_digest":"' + BUILD_DIGEST + '"}',
                    "",
                )
            if cmd[-1] == "{{.Architecture}}":
                return 0, "amd64", ""
        if cmd[:5] == [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/venv/bin/omnigent",
        ]:
            return 0, "omnigent 0.12.0 (built for test)\n", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images(
        {"OMNIGENT_BUILD_DIGEST": "sha256:" + "4" * 64}
    )

    assert resolved.omnigent_build_digest == "sha256:" + "4" * 64
    assert resolved.details["buildIdentitySource"] == "operator-quarantine"
    assert resolved.details["opencodeHostCompatibility"] == {
        "status": "blocked",
        "failureCode": "omnigent_operator_host_build_mismatch",
        "serverImageRef": SERVER_REF,
        "hostImageRef": HOST_REF,
        "serverBuildDigest": BUILD_DIGEST,
        "hostBuildDigest": BUILD_DIGEST,
        "serverVersion": "0.12.0",
        "hostVersion": "0.12.0",
    }


@pytest.mark.asyncio
async def test_resolution_accepts_an_explicit_independently_paired_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator build authority need not equal a registry manifest digest."""

    from moonmind.omnigent.bootstrap import store

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        if image_env == "OMNIGENT_IMAGE":
            return SERVER_REF, "sha256:" + "1" * 64
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "2" * 64
        return None, None

    async def build_identity(_image_ref):
        return BUILD_DIGEST

    async def version(_image_ref):
        return "0.12.0"

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            return 0, "amd64", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_image_build_identity", build_identity)
    monkeypatch.setattr(image_resolution, "_image_omnigent_version", version)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images(
        {"OMNIGENT_BUILD_DIGEST": BUILD_DIGEST}
    )

    assert resolved.omnigent_build_digest == BUILD_DIGEST
    assert resolved.details["buildIdentitySource"] == "operator"
    assert resolved.details["opencodeHostCompatibility"]["status"] == "ready"


@pytest.mark.asyncio
async def test_resolution_quarantines_server_and_host_build_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the 0.12 server / 0.11 OpenCode host production failure."""

    from moonmind.omnigent.bootstrap import store

    server_build = "sha256:" + "1" * 64
    stale_host_build = "sha256:" + "2" * 64

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        if image_env == "OMNIGENT_IMAGE":
            return SERVER_REF, server_build
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "3" * 64
        return None, None

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            if cmd[-1] == "{{json .Config.Labels}}":
                return (
                    0,
                    '{"moonmind.omnigent.build_digest":"' + stale_host_build + '"}',
                    "",
                )
            if cmd[-1] == "{{.Architecture}}":
                return 0, "arm64", ""
        if cmd[:5] == [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/venv/bin/omnigent",
        ]:
            version = "0.12.0" if cmd[-2] == SERVER_REF else "0.11.0"
            return 0, f"omnigent {version} (built for test)\n", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images({})

    assert resolved.server_image_ref == SERVER_REF
    assert resolved.opencode_host_image_ref == HOST_REF
    assert resolved.omnigent_build_digest == server_build
    assert resolved.details["opencodeHostCompatibility"] == {
        "status": "blocked",
        "failureCode": "omnigent_server_host_build_mismatch",
        "serverImageRef": SERVER_REF,
        "hostImageRef": HOST_REF,
        "serverBuildDigest": server_build,
        "hostBuildDigest": stale_host_build,
        "serverVersion": "0.12.0",
        "hostVersion": "0.11.0",
    }


@pytest.mark.asyncio
async def test_resolution_quarantines_mislabeled_host_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.bootstrap import store

    async def resolve_image(image_env, tag_env, ref_env, env=None):
        del tag_env, ref_env, env
        if image_env == "OMNIGENT_IMAGE":
            return SERVER_REF, BUILD_DIGEST
        if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE":
            return HOST_REF, "sha256:" + "2" * 64
        return None, None

    async def run(cmd, timeout=30):
        del timeout
        if cmd[:4] == ["docker", "image", "inspect", HOST_REF]:
            if cmd[-1] == "{{json .Config.Labels}}":
                return (
                    0,
                    '{"moonmind.omnigent.build_digest":"' + BUILD_DIGEST + '"}',
                    "",
                )
            if cmd[-1] == "{{.Architecture}}":
                return 0, "amd64", ""
        if cmd[:5] == [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/venv/bin/omnigent",
        ]:
            version = "0.12.0" if cmd[-2] == SERVER_REF else "0.11.0"
            return 0, f"omnigent {version} (built for test)\n", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve_image)
    monkeypatch.setattr(image_resolution, "_run", run)
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    resolved = await image_resolution.resolve_omnigent_images({})

    compatibility = resolved.details["opencodeHostCompatibility"]
    assert compatibility["status"] == "blocked"
    assert compatibility["failureCode"] == "omnigent_server_host_version_mismatch"


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
    monkeypatch.setattr(store, "load_resolved_state", _state)
    assert host_classes.get_opencode_host_image_ref() == HOST_REF

    # A placeholder digest in persisted state still fails closed.
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: _state(opencodeHostImageRef="ghcr.io/x/y@sha256:" + "0" * 64),
    )
    with pytest.raises(Exception):
        host_classes.get_opencode_host_image_ref()


def test_selectors_reject_a_quarantined_server_host_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All planning and worker processes consume the resolver's verdict."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform import host_classes

    blocked = _state(
        details={
            "opencodeHostCompatibility": {
                "status": "blocked",
                "failureCode": "omnigent_server_host_build_mismatch",
                "serverImageRef": SERVER_REF,
                "hostImageRef": HOST_REF,
            }
        }
    )
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", HOST_REF)
    monkeypatch.setattr(store, "load_resolved_state", lambda: blocked)

    with pytest.raises(
        Exception,
        match="OpenCode Host Class is quarantined.*server_host_build_mismatch",
    ):
        host_classes.get_opencode_host_image_ref()


def test_selectors_reject_compatibility_evidence_for_a_different_image_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready verdict cannot authorize refs selected after reconciliation."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform import host_classes

    changed_host = "ghcr.io/example/opencode-host@sha256:" + "5" * 64
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", SERVER_REF)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", changed_host)
    monkeypatch.setattr(store, "load_resolved_state", _state)

    with pytest.raises(Exception, match="evidence does not match"):
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
