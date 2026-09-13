"""Major.minor interoperability keeps exact deployment and plan evidence."""

from types import SimpleNamespace

import pytest

from moonmind.omnigent import deployment_identity
from moonmind.omnigent.bootstrap import image_resolution, store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.compatibility import versions_compatible


@pytest.mark.parametrize(
    ("expected", "observed", "compatible"),
    [
        ("0.12.0", "0.12.37", True),
        ("1.2.99", "1.2.0", True),
        ("0.12", "omnigent 0.12.1", True),
        ("0.12.0", "0.13.0", False),
        ("1.12.0", "2.12.0", False),
        ("0.12.0", "0.120.0", False),
        ("0.12.0", "", False),
        ("0.12.0", "0.12.1garbage", False),
        ("", "", False),
        ("0.12.0", "0.12.1rc1", False),
    ],
)
def test_release_series(expected, observed, compatible):
    assert versions_compatible(expected, observed) is compatible


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server_version,host_version,code",
    [
        ("0.12.0", "0.12.9", None),
        ("0.12.9", "0.12.0", None),
        ("0.12.0", "0.13.0", "omnigent_server_host_version_mismatch"),
    ],
)
async def test_independently_built_hosts_share_a_release_series(
    monkeypatch, server_version, host_version, code
):
    async def build(_):
        return "sha256:" + "b" * 64

    async def version(_):
        return host_version

    async def ready(_):
        return True

    monkeypatch.setattr(image_resolution, "_image_build_identity", build)
    monkeypatch.setattr(image_resolution, "_image_omnigent_version", version)
    monkeypatch.setattr(image_resolution, "_image_opencode_bootstrap_ready", ready)
    result = await image_resolution._evaluate_opencode_host(
        "host@sha256:" + "c" * 64,
        server_ref="server@sha256:" + "a" * 64,
        server_image_digest="sha256:" + "a" * 64,
        server_version=server_version,
        configured_build_digest="",
    )
    assert result.failure_code == code
    assert result.build_digest == "sha256:" + "b" * 64


@pytest.mark.parametrize("version,compatible", [("0.12.9", True), ("0.13.0", False)])
def test_admitted_plan_survives_patch_update_without_replacing_host(
    tmp_path, monkeypatch, version, compatible
):
    monkeypatch.delenv("OMNIGENT_BUILD_DIGEST", raising=False)
    monkeypatch.delenv("OMNIGENT_IMAGE_REF", raising=False)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "resolved.json")
    )
    digest = "sha256:" + "b" * 64
    store.save_resolved_state(
        ResolvedOmnigentDeploymentState(
            serverImageRef="server@" + digest,
            omnigentBuildDigest=digest,
            details={
                "opencodeHostCompatibility": {
                    "serverVersion": version,
                    "serverBuildDigest": digest,
                }
            },
        )
    )
    plan = SimpleNamespace(
        executionRealizerRef="generic-omnigent-host@1",
        omnigentVersion="0.12.0",
        supportIdentity=SimpleNamespace(omnigentServerBuildRef="sha256:" + "a" * 64),
        hostImageRef="old-host@sha256:" + "c" * 64,
    )
    if compatible:
        deployment_identity.assert_plan_matches_deployed_runtime(plan)
        assert plan.hostImageRef == "old-host@sha256:" + "c" * 64
    else:
        with pytest.raises(deployment_identity.OmnigentDeploymentIdentityConflict):
            deployment_identity.assert_plan_matches_deployed_runtime(plan)


def test_missing_deployment_is_retryable_but_invalid_override_is_not(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "missing.json")
    )
    monkeypatch.delenv("OMNIGENT_IMAGE_REF", raising=False)
    monkeypatch.delenv("OMNIGENT_BUILD_DIGEST", raising=False)
    with pytest.raises(deployment_identity.OmnigentDeploymentNotReady):
        deployment_identity.resolve_deployed_server_build_digest()
    monkeypatch.setenv("OMNIGENT_BUILD_DIGEST", "invalid")
    with pytest.raises(Exception) as error:
        deployment_identity.resolve_deployed_server_build_digest()
    assert not isinstance(error.value, deployment_identity.OmnigentDeploymentNotReady)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,expected",
    [
        ("omnigent 0.12.9 (built 2026-09-13T00:00:00Z)\n", "0.12.9"),
        ("omnigent 0.12.9.dev1", None),
        ("omnigent 0.12.9.5", None),
        ("omnigent 0.12.9 garbage", None),
    ],
)
async def test_binary_version_probe_does_not_truncate_unknown_versions(
    monkeypatch, output, expected
):
    async def run(*args, **kwargs):
        return 0, output, ""

    monkeypatch.setattr(image_resolution, "_run", run)
    assert (
        await image_resolution._image_omnigent_version("host@sha256:" + "a" * 64)
        == expected
    )


@pytest.mark.parametrize(
    "harness_id", ["opencode-native", "codex-native", "claude-native", "pi-native"]
)
def test_host_classes_keep_host_provenance_independent_of_server(
    monkeypatch, harness_id
):
    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness
    from moonmind.omnigent.harness_platform.host_classes import (
        OmnigentHostClassSelector,
    )

    image = "host@sha256:" + "b" * 64
    host_digest = "sha256:" + "c" * 64
    state = ResolvedOmnigentDeploymentState(
        details={
            "opencodeHostCompatibility": {
                "status": "ready",
                "hostImageRef": image,
                "hostBuildDigest": host_digest,
            }
        }
    )
    monkeypatch.setattr(store, "load_resolved_state", lambda: state)
    catalog = _normalize_harness(
        {"id": harness_id},
        omnigent_version="0.12.0",
        omnigent_build_digest="sha256:" + "a" * 64,
    )
    selector = OmnigentHostClassSelector(
        environment={
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": image,
            "OMNIGENT_SHARED_HOST_IMAGE_REF": image,
            "OMNIGENT_PI_HOST_IMAGE_REF": image,
        }
    )
    host = selector.select(
        harness=catalog,
        omnigent_version="0.12.0",
        omnigent_build_digest="sha256:" + "a" * 64,
        integration_mode="native-server",
        materializer_refs=["none@1"],
    )
    assert host.omnigentBuildDigest == host_digest
    assert host.imageRef == image
