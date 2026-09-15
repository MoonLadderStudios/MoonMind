"""Major.minor interoperability keeps exact deployment and plan evidence."""

from types import SimpleNamespace

import pytest

from moonmind.omnigent import deployment_identity
from moonmind.omnigent.bootstrap import image_resolution, store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.compatibility import (
    image_repository,
    is_same_image_repository,
    vendor_versions_compatible,
    versions_compatible,
)
from moonmind.omnigent.host_image_drift import (
    compatible_deployed_fallback,
    is_compatible_image_drift,
)
from tests.unit.omnigent.test_harness_platform import (  # noqa: F401 -- fixture for serialized admitted plans
    _test_owned_host_classes,
)


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
@pytest.mark.asyncio
async def test_admitted_plan_survives_patch_update_without_replacing_host(
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
        await deployment_identity.assert_plan_matches_deployed_runtime(plan)
        assert plan.hostImageRef == "old-host@sha256:" + "c" * 64
    else:
        with pytest.raises(deployment_identity.OmnigentDeploymentIdentityConflict):
            await deployment_identity.assert_plan_matches_deployed_runtime(plan)


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
                "hostVersion": "0.12.9",
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
        integration_mode="native-server",
        materializer_refs=["none@1"],
    )
    assert host.omnigentBuildDigest == host_digest
    assert host.imageRef == image


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness_id", ["opencode-native", "codex-native", "claude-native", "pi-native"]
)
async def test_bootstrap_selects_each_images_actual_provenance(
    tmp_path, monkeypatch, harness_id
):
    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness
    from moonmind.omnigent.harness_platform.host_classes import (
        OmnigentHostClassSelector,
    )
    from moonmind.omnigent.host_services.attestation import _assert_exact_omnigent_build

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "images.json")
    )
    refs = {
        "OMNIGENT_IMAGE": "server@sha256:" + "a" * 64,
        "OMNIGENT_OPENCODE_HOST_IMAGE": "opencode@sha256:" + "b" * 64,
        "OMNIGENT_SHARED_HOST_IMAGE": "shared@sha256:" + "d" * 64,
        "OMNIGENT_PI_HOST_IMAGE": "pi@sha256:" + "e" * 64,
    }
    builds = {
        ref: "sha256:" + character * 64 for ref, character in zip(refs.values(), "1678")
    }

    async def resolve(image_env, *_args):
        ref = refs[image_env]
        return ref, "sha256:" + ref.rsplit(":", 1)[-1]

    async def run(argv, **_kwargs):
        import json

        if argv[:3] == ["docker", "image", "inspect"]:
            if argv[-1] == "{{json .Config.Labels}}":
                return (
                    0,
                    json.dumps({"moonmind.omnigent.build_digest": builds[argv[3]]}),
                    "",
                )
            return 0, "amd64", ""
        if argv[:3] == ["docker", "run", "--rm"]:
            return 0, "omnigent 0.12.9", ""
        if argv[0] == "sh":
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(image_resolution, "_resolve_image", resolve)
    monkeypatch.setattr(image_resolution, "_run", run)
    state = await image_resolution.resolve_omnigent_images({})
    store.save_resolved_state(state)
    catalog = _normalize_harness(
        {"id": harness_id},
        omnigent_version="0.12.0",
        omnigent_build_digest="sha256:" + "a" * 64,
    )
    selector = OmnigentHostClassSelector(environment={})
    host = selector.select(
        harness=catalog,
        omnigent_version="0.12.0",
        integration_mode="native-server",
        materializer_refs=["none@1"],
        requested_host_class_ref=(
            "omnigent-opencode@1" if harness_id == "opencode-native" else None
        ),
    )
    expected = refs[
        (
            "OMNIGENT_PI_HOST_IMAGE"
            if harness_id == "pi-native"
            else (
                "OMNIGENT_OPENCODE_HOST_IMAGE"
                if harness_id == "opencode-native"
                else "OMNIGENT_SHARED_HOST_IMAGE"
            )
        )
    ]
    assert host.imageRef == expected
    assert host.omnigentBuildDigest == builds[expected]
    _assert_exact_omnigent_build(
        {"Config": {"Labels": {"moonmind.omnigent.build_digest": builds[expected]}}},
        host.omnigentBuildDigest,
    )
    assert set(state.details["hostImageProvenance"]) == set(refs.values()) - {
        refs["OMNIGENT_IMAGE"]
    }

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    with pytest.raises(HarnessPlatformError, match="violates OMNIGENT_BUILD_DIGEST"):
        OmnigentHostClassSelector(
            environment={"OMNIGENT_BUILD_DIGEST": "sha256:" + "9" * 64}
        ).select(
            harness=catalog,
            omnigent_version="0.12.0",
            integration_mode="native-server",
            materializer_refs=["none@1"],
            requested_host_class_ref=host.ref,
        )

    # Missing or foreign evidence must not substitute server provenance.
    state.details["hostImageProvenance"][expected] = {
        "buildDigest": None,
        "version": "0.12.9",
    }
    store.save_resolved_state(state)
    with pytest.raises(HarnessPlatformError, match="build provenance"):
        selector.select(
            harness=catalog,
            omnigent_version="0.12.0",
            integration_mode="native-server",
            materializer_refs=["none@1"],
            requested_host_class_ref=host.ref,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement_version,compatible", [("1.0.99", True), ("1.1.0", False)]
)
async def test_host_override_never_masks_server_replacement(
    tmp_path, monkeypatch, replacement_version, compatible
):
    import json
    import os

    from tests.unit.omnigent.test_harness_platform import _compile_opencode_plan

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "images.json")
    )
    monkeypatch.setattr(image_resolution, "_operator_image_baseline", None)
    for key in image_resolution._PUBLISHED_IMAGE_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OMNIGENT_IMAGE", "server")
    host_pin = "sha256:" + "e" * 64
    monkeypatch.setenv("OMNIGENT_BUILD_DIGEST", host_pin)
    # A host pin alone is not discovery of a server.
    assert store.load_resolved_state() is None
    with pytest.raises(deployment_identity.OmnigentDeploymentNotReady):
        deployment_identity.resolve_deployed_server_build_digest()
    original_digest = "sha256:" + "b" * 64
    replacement_digest = "sha256:" + "d" * 64
    observed = {"digest": original_digest, "version": "1.0.0"}
    host_ref = "host@sha256:" + "f" * 64

    async def running_server(*_args):
        return "server@" + observed["digest"]

    async def resolve(image_env, *_args):
        return (
            (host_ref, "sha256:" + "f" * 64)
            if image_env == "OMNIGENT_OPENCODE_HOST_IMAGE"
            else (None, None)
        )

    async def run(argv, **_kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            if argv[-1] == "{{json .Config.Labels}}":
                return 0, json.dumps({"moonmind.omnigent.build_digest": host_pin}), ""
            return 0, "amd64", ""
        if argv[:3] == ["docker", "run", "--rm"]:
            return (
                0,
                "omnigent "
                + (observed["version"] if argv[-2].startswith("server@") else "1.0.9"),
                "",
            )
        if argv[0] == "sh":
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(
        image_resolution, "_resolve_running_server_image", running_server
    )
    monkeypatch.setattr(image_resolution, "_resolve_image", resolve)
    monkeypatch.setattr(image_resolution, "_run", run)
    first = await image_resolution.publish_resolved_omnigent_images()
    assert first.omnigent_build_digest == original_digest
    assert deployment_identity.resolve_deployed_server_build_digest() == original_digest
    # A serialized admitted plan contains the actual catalog server identity.
    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanEnvelope,
    )

    plan = _compile_opencode_plan()
    plan = OmnigentExecutionPlanEnvelope.model_validate_json(
        plan.model_dump_json(by_alias=True)
    )
    assert plan.payload.supportIdentity.omnigentServerBuildRef == original_digest
    await deployment_identity.assert_plan_matches_deployed_runtime(plan.payload)

    from unittest.mock import AsyncMock
    from moonmind.omnigent.harness_platform.catalog_service import DbHarnessCatalogRepository

    monkeypatch.setattr(DbHarnessCatalogRepository, "load", AsyncMock(return_value=SimpleNamespace(
        snapshot=SimpleNamespace(
            catalogRef=plan.payload.harnessCatalogRef,
            endpointRef=plan.payload.endpointRef,
            omnigentBuildDigest=original_digest,
            omnigentVersion="1.0.0",
        ),
    )))

    observed.update(digest=replacement_digest, version=replacement_version)
    current = await image_resolution.publish_resolved_omnigent_images()
    assert current.omnigent_build_digest == replacement_digest
    assert image_resolution.resolved_build_digest(current) == replacement_digest
    assert os.environ["OMNIGENT_BUILD_DIGEST"] == host_pin
    # Historical persisted discovery can contain a host pin in this field; the
    # server image remains authoritative when reading that old payload.
    store.save_resolved_state(
        current.model_copy(update={"omnigent_build_digest": host_pin})
    )
    assert (
        deployment_identity.resolve_deployed_server_build_digest() == replacement_digest
    )
    if compatible:
        await deployment_identity.assert_plan_matches_deployed_runtime(plan.payload)
    else:
        with pytest.raises(deployment_identity.OmnigentDeploymentIdentityConflict):
            await deployment_identity.assert_plan_matches_deployed_runtime(plan.payload)


@pytest.mark.parametrize(
    ("ref", "expected_repo"),
    [
        (
            "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "a" * 64,
            "ghcr.io/moonladderstudios/omnigent-host-moonmind",
        ),
        (
            "ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11",
            "ghcr.io/moonladderstudios/omnigent-host-moonmind",
        ),
        ("", ""),
        (None, ""),
    ],
)
def test_image_repository_extraction(ref, expected_repo):
    assert image_repository(ref) == expected_repo


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        (
            "ghcr.io/org/img@sha256:" + "a" * 64,
            "ghcr.io/org/img@sha256:" + "b" * 64,
            True,
        ),
        (
            "ghcr.io/org/img:1.18.11",
            "ghcr.io/org/img@sha256:" + "c" * 64,
            True,
        ),
        (
            "ghcr.io/org/img@sha256:" + "a" * 64,
            "ghcr.io/org/other@sha256:" + "a" * 64,
            False,
        ),
        ("", "ghcr.io/org/img@sha256:" + "a" * 64, False),
        ("", "", False),
    ],
)
def test_same_image_repository(first, second, same):
    assert is_same_image_repository(first, second) is same
    assert is_compatible_image_drift(first, second) is same


@pytest.mark.parametrize(
    ("pinned", "observed", "compatible"),
    [
        ("1.18.11", "1.18.11", True),
        ("1.18.11", "1.18.12", True),
        ("1.18.11", "opencode version 1.18.12", True),
        ("1.18.11", "1.19.0", False),
        ("1.18.11", "2.18.11", False),
        ("1.18.11", "", False),
        ("", "1.18.11", False),
        ("0.104.0", "0.104.5", True),
        ("0.104.0", "0.105.0", False),
    ],
)
def test_vendor_patch_drift_is_compatible(pinned, observed, compatible):
    assert vendor_versions_compatible(pinned, observed) is compatible


def test_compatible_deployed_fallback_prefers_same_repo():
    requested = "ghcr.io/example/opencode@sha256:" + "a" * 64
    current = "ghcr.io/example/opencode@sha256:" + "b" * 64
    foreign = "ghcr.io/example/other@sha256:" + "c" * 64
    assert (
        compatible_deployed_fallback(requested, deployed_refs=[current, foreign])
        == current
    )
    assert (
        compatible_deployed_fallback(requested, deployed_refs=[foreign]) is None
    )
    assert (
        compatible_deployed_fallback(requested, deployed_refs=[requested]) is None
    )
    assert compatible_deployed_fallback("ghcr.io/example/opencode:latest") is None
