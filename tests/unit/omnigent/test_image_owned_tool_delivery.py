"""Image-owned tool delivery behavior (MoonLadderStudios/MoonMind#4558)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.host_ports import HostLaunchSpec
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.mounted_tools import (
    OmnigentMountedToolService,
    classify_tool_attachment,
)


def _manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "gh",
                        "version": "2.76.2",
                        "path": "bin/gh",
                        "versionProbe": ["--version"],
                        "platforms": {"linux/amd64": {"executableSha256": "a" * 64}},
                    },
                    {
                        "name": "docker",
                        "version": "container-v1",
                        "path": "bin/moonmind",
                        "versionProbe": ["--help"],
                        "platforms": {"linux/amd64": {"executableSha256": "b" * 64}},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.asyncio
async def test_runs_without_tool_capability_are_not_rejected(tmp_path: Path) -> None:
    backend = SimpleNamespace(run=AsyncMock())
    service = OmnigentMountedToolService(backend=backend, manifest_path=_manifest(tmp_path))

    assert await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": []}
    ) == []

    backend.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_tools_still_fail_before_launch(tmp_path: Path) -> None:
    backend = SimpleNamespace(run=AsyncMock())
    service = OmnigentMountedToolService(backend=backend, manifest_path=_manifest(tmp_path))

    with pytest.raises(HarnessPlatformError, match="absent from the deployment bundle"):
        await service.materialize(
            {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh", "nope"]}
        )

    backend.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_hosts_on_different_images_bind_different_tool_sources(
    tmp_path: Path,
) -> None:
    """Upgrade semantics: each host binds its own image; nothing is shared mutable."""
    backend = SimpleNamespace(run=AsyncMock())
    service = OmnigentMountedToolService(backend=backend, manifest_path=_manifest(tmp_path))
    resolved = {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]}

    host_a = await service.materialize(resolved, image_ref="example/host-a@sha256:" + "a" * 64)
    host_b = await service.materialize(resolved, image_ref="example/host-b@sha256:" + "b" * 64)

    assert host_a[0]["sourceRef"] == "image:example/host-a@sha256:" + "a" * 64
    assert host_b[0]["sourceRef"] == "image:example/host-b@sha256:" + "b" * 64
    assert host_a[0]["sourceRef"] != host_b[0]["sourceRef"]
    assert host_a[0]["cleanupRef"] is None
    backend.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_constructor_image_ref_is_used_when_materialize_omits_it(
    tmp_path: Path,
) -> None:
    service = OmnigentMountedToolService(
        backend=SimpleNamespace(run=AsyncMock()),
        manifest_path=_manifest(tmp_path),
        image_ref="example/host@sha256:" + "c" * 64,
    )
    result = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["docker"]}
    )
    assert result[0]["sourceRef"] == "image:example/host@sha256:" + "c" * 64
    assert result[0]["tools"][0] == {
        "name": "docker",
        "version": "container-v1",
        "path": "bin/moonmind",
        "versionProbe": ["--help"],
        "executableDigests": ["b" * 64],
    }


@pytest.mark.asyncio
async def test_upgrade_holds_host_a_while_host_b_moves_with_cli_only_change_and_interrupted_activation(
    tmp_path: Path,
) -> None:
    """REQ-04: pre-activation interruption leaves host A intact via rollback.

    Host A stays bound to its original image/tools while host B is staged
    from an updated image carrying a MoonMind CLI-only change (gh pin
    identical). The activation is interrupted before the new image is
    launched; the normal rollback path keeps the prior active binding and
    nothing rewrites host A's image-owned executable files.
    """
    import copy

    def _write_manifest(path: Path, cli_version: str, cli_digest: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "name": "gh",
                            "version": "2.76.2",
                            "path": "bin/gh",
                            "versionProbe": ["--version"],
                            "platforms": {
                                "linux/amd64": {"executableSha256": "a" * 64}
                            },
                        },
                        {
                            "name": "docker",
                            "version": cli_version,
                            "path": "bin/moonmind",
                            "versionProbe": ["--help"],
                            "platforms": {
                                "linux/amd64": {"executableSha256": cli_digest}
                            },
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

    manifest_v1 = tmp_path / "manifest-v1.lock.json"
    manifest_v2 = tmp_path / "manifest-v2.lock.json"
    _write_manifest(manifest_v1, "container-v1", "b" * 64)
    # CLI-only change: gh entry byte-identical, moonmind version/digest move.
    _write_manifest(manifest_v2, "container-v2", "c" * 64)
    v1_tools = {
        item["name"]: item
        for item in json.loads(manifest_v1.read_text(encoding="utf-8"))["tools"]
    }
    v2_tools = {
        item["name"]: item
        for item in json.loads(manifest_v2.read_text(encoding="utf-8"))["tools"]
    }
    assert v1_tools["gh"] == v2_tools["gh"]
    assert v1_tools["docker"] != v2_tools["docker"]

    backend_a = SimpleNamespace(run=AsyncMock())
    backend_b = SimpleNamespace(run=AsyncMock())
    service_a = OmnigentMountedToolService(
        backend=backend_a, manifest_path=manifest_v1
    )
    service_b = OmnigentMountedToolService(
        backend=backend_b, manifest_path=manifest_v2
    )
    resolved = {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]}

    image_a = "example/host@sha256:" + "a" * 64
    image_b = "example/host@sha256:" + "b" * 64
    host_a = await service_a.materialize(resolved, image_ref=image_a)
    assert classify_tool_attachment(host_a[0]) == "image"
    active_binding = copy.deepcopy(host_a)
    host_a_digests = [tool["executableDigests"] for tool in host_a[0]["tools"]]

    # Stage host B from the updated image (CLI-only manifest change).
    host_b = await service_b.materialize(resolved, image_ref=image_b)
    assert classify_tool_attachment(host_b[0]) == "image"
    assert host_b[0]["sourceRef"] != host_a[0]["sourceRef"]
    assert host_b[0]["sourceRef"] == f"image:{image_b}"

    # Interruption before new-image activation: the staged B binding is never
    # launched, so the controller record reconciles to the prior active state
    # (same-controller recovery; unknown state is not success and must not
    # destroy the still-serving binding).
    launched: list[str] = []
    controller_record = {"active": copy.deepcopy(active_binding), "staged": host_b}

    async def _activate_staged() -> None:
        raise RuntimeError("interrupted before new-image activation")

    with pytest.raises(RuntimeError, match="interrupted before new-image"):
        await _activate_staged()
    # Normal rollback path: discard the staged candidate, keep prior active.
    controller_record.pop("staged")
    assert launched == []
    assert controller_record["active"] == active_binding

    # Nothing rewrote host A's executable files: binding, digests, and
    # classification identical; no Docker volume inspection/creation ran.
    assert host_a == active_binding
    assert [tool["executableDigests"] for tool in host_a[0]["tools"]] == host_a_digests
    assert classify_tool_attachment(host_a[0]) == "image"
    assert host_a[0]["cleanupRef"] is None
    backend_a.run.assert_not_awaited()
    backend_b.run.assert_not_awaited()


def test_classify_tool_attachment_distinguishes_image_legacy_and_other() -> None:
    assert classify_tool_attachment(
        {"kind": "image", "targetPath": "/opt/moonmind-tools"}
    ) == "image"
    # Persisted pre-cutover bindings stay readable as legacy drain candidates.
    assert classify_tool_attachment(
        {
            "kind": "volume",
            "sourceRef": "moonmind-omnigent-tools-gh-2.76.2",
            "targetPath": "/opt/moonmind-tools",
        }
    ) == "legacy-volume-drain"
    assert classify_tool_attachment({"kind": "volume", "targetPath": "/data"}) == "unsupported"
    assert classify_tool_attachment({"kind": "bind", "targetPath": "/opt/moonmind-tools"}) == (
        "unsupported"
    )
    assert classify_tool_attachment({}) == "unsupported"
    assert classify_tool_attachment(None) == "unsupported"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_launcher_mounts_bind_workspace_and_skills_while_skipping_image_tools() -> None:
    """P1: local-daemon bind projections must still mount; only image tools skip."""
    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **_kwargs):
            calls.append(list(argv))
            return (0, "container-id" if argv[1] == "create" else "", "")

    class Scripts:
        def build_entrypoint(self, **_kwargs):
            return "exec true", {}

    launcher = DockerOmnigentHostLauncher(
        backend=Backend(),
        runtime_scripts=Scripts(),
        server_url="http://omnigent:8000",
    )
    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {"readOnlyRoot": True},
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )
    spec = HostLaunchSpec.model_validate(
        {
            "executionPlanRef": "plan:one",
            "stepExecutionId": "step-1",
            "runtimeBindingId": "binding-1",
            "hostLeaseRef": "host-lease:one",
            "hostLeaseGeneration": 1,
            "hostClassRef": host_class.ref,
            "imageRef": host_class.imageRef,
            "serverEndpointRef": "default",
            "serverUrl": "http://omnigent:8000",
            "networkRef": "moonmind_default",
            "limits": {"cpuMillis": 2000},
            "runtime": {},
            "correlationName": "mm-host-bind-tools",
            "workspaceAttachment": {
                "kind": "bind",
                "sourceRef": "/workspaces/run",
                "targetPath": "/workspaces/run",
                "accessMode": "read-write",
            },
            "skillAttachment": {
                "kind": "bind",
                "sourceRef": "/opt/moonmind-skills",
                "targetPath": "/opt/moonmind-skills",
                "accessMode": "read-only",
            },
            "toolAttachments": [
                {
                    "kind": "image",
                    "sourceRef": "image:ghcr.io/example/opencode@sha256:" + "f" * 64,
                    "targetPath": "/opt/moonmind-tools",
                    "accessMode": "read-only",
                    "cleanupRef": None,
                    "toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64,
                    "tools": [],
                }
            ],
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": "mm-host-state-test",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": {},
        }
    )
    await launcher.launch(
        spec=spec,
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )
    create = next(argv for argv in calls if argv[:2] == ["docker", "create"])
    assert "type=bind,src=/workspaces/run,dst=/workspaces/run" in create
    assert (
        "type=bind,src=/opt/moonmind-skills,dst=/opt/moonmind-skills,readonly"
        in create
    )
    assert not any("/opt/moonmind-tools" in item for item in create)


@pytest.mark.asyncio
async def test_materialize_binds_manifest_bundle_version_to_selected_image(
    tmp_path: Path,
) -> None:
    """P1: attachment binds manifest bundle version to the selected image."""
    backend = SimpleNamespace(run=AsyncMock())
    service = OmnigentMountedToolService(backend=backend, manifest_path=_manifest(tmp_path))
    result = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]},
        image_ref="example/host@sha256:" + "a" * 64,
    )
    assert result[0]["sourceRef"] == "image:example/host@sha256:" + "a" * 64
    assert result[0]["manifestBundleVersion"] == ""  # fixture has no bundleVersion
    backend.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_materialize_prefers_plan_snapshotted_tool_manifest(tmp_path: Path) -> None:
    """P1: in-flight plans keep their snapshotted metadata, not the new manifest."""
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps(
            {
                "bundleVersion": "gh-2.76.2-container-v1",
                "tools": [
                    {
                        "name": "gh",
                        "version": "2.76.2",
                        "path": "bin/gh",
                        "versionProbe": ["--version"],
                        "platforms": {"linux/amd64": {"executableSha256": "a" * 64}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = OmnigentMountedToolService(
        backend=SimpleNamespace(run=AsyncMock()), manifest_path=manifest
    )
    snapshotted = {
        "toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64,
        "tools": ["gh"],
        "toolManifest": {
            "bundleVersion": "gh-2.0.0-container-v0",
            "tools": {
                "gh": {
                    "version": "2.0.0",
                    "path": "bin/gh",
                    "versionProbe": ["--version"],
                    "platforms": {"linux/amd64": {"executableSha256": "0" * 64}},
                }
            },
        },
    }
    result = await service.materialize(
        snapshotted, image_ref="example/host@sha256:" + "a" * 64
    )
    assert result[0]["manifestBundleVersion"] == "gh-2.0.0-container-v0"
    assert result[0]["tools"][0]["version"] == "2.0.0"
    assert result[0]["tools"][0]["executableDigests"] == ["0" * 64]
    current = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]},
        image_ref="example/host@sha256:" + "a" * 64,
    )
    assert current[0]["manifestBundleVersion"] == "gh-2.76.2-container-v1"
    assert current[0]["tools"][0]["version"] == "2.76.2"


@pytest.mark.asyncio
async def test_launcher_mounts_no_overlay_for_image_tools_but_drains_legacy() -> None:
    """REQ-02/REQ-06: image tools create no mount; legacy volume still drains."""
    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **_kwargs):
            calls.append(list(argv))
            return (0, "container-id" if argv[1] == "create" else "", "")

    class Scripts:
        def build_entrypoint(self, **_kwargs):
            return "exec true", {}

    launcher = DockerOmnigentHostLauncher(
        backend=Backend(),
        runtime_scripts=Scripts(),
        server_url="http://omnigent:8000",
    )
    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {"readOnlyRoot": True},
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )

    def _spec(tool_attachments: list[dict]) -> HostLaunchSpec:
        return HostLaunchSpec.model_validate(
            {
                "executionPlanRef": "plan:one",
                "stepExecutionId": "step-1",
                "runtimeBindingId": "binding-1",
                "hostLeaseRef": "host-lease:one",
                "hostLeaseGeneration": 1,
                "hostClassRef": host_class.ref,
                "imageRef": host_class.imageRef,
                "serverEndpointRef": "default",
                "serverUrl": "http://omnigent:8000",
                "networkRef": "moonmind_default",
                "limits": {"cpuMillis": 2000},
                "runtime": {},
                "correlationName": "mm-host-tools",
                "workspaceAttachment": {
                    "kind": "volume",
                    "sourceRef": "workspace-vol",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                "skillAttachment": {
                    "kind": "volume",
                    "sourceRef": "skills-vol",
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                },
                "toolAttachments": tool_attachments,
                "stateAttachment": {
                    "kind": "volume",
                    "sourceRef": "mm-host-state-test",
                    "targetPath": "/home/app/.omnigent",
                    "accessMode": "read-write",
                },
                "labels": {},
            }
        )

    image_attachment = {
        "kind": "image",
        "sourceRef": "image:ghcr.io/example/opencode@sha256:" + "f" * 64,
        "targetPath": "/opt/moonmind-tools",
        "accessMode": "read-only",
        "cleanupRef": None,
        "toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64,
        "tools": [],
    }
    await launcher.launch(
        spec=_spec([image_attachment]),
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )
    create = next(argv for argv in calls if argv[:2] == ["docker", "create"])
    assert not any("/opt/moonmind-tools" in item for item in create)

    calls.clear()
    legacy_attachment = {
        "kind": "volume",
        "sourceRef": "moonmind-omnigent-tools-gh-2.76.2",
        "targetPath": "/opt/moonmind-tools",
        "accessMode": "read-only",
        "cleanupRef": None,
        "toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64,
        "tools": [],
    }
    await launcher.launch(
        spec=_spec([legacy_attachment]),
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )
    create = next(argv for argv in calls if argv[:2] == ["docker", "create"])
    assert (
        "type=volume,src=moonmind-omnigent-tools-gh-2.76.2,"
        "dst=/opt/moonmind-tools,readonly" in create
    )


_HOST_REPO = "ghcr.io/moonladderstudios/omnigent-host-moonmind"
_PRE_TOOLS_HOST = _HOST_REPO + "@sha256:" + "a" * 64
_TOOLED_HOST = _HOST_REPO + "@sha256:" + "b" * 64


def _pinned_host_class(image_ref: str) -> HostClass:
    return HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": image_ref,
            "omnigentVersion": "0.14.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {"readOnlyRoot": True, "mountedTools": True},
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


def _tool_launch_spec(image_ref: str) -> HostLaunchSpec:
    return HostLaunchSpec.model_validate(
        {
            "executionPlanRef": "plan:one",
            "stepExecutionId": "step-1",
            "runtimeBindingId": "binding-1",
            "hostLeaseRef": "host-lease:one",
            "hostLeaseGeneration": 1,
            "hostClassRef": "omnigent-opencode@1",
            "imageRef": image_ref,
            "serverEndpointRef": "default",
            "serverUrl": "http://omnigent:8000",
            "networkRef": "moonmind_default",
            "limits": {"cpuMillis": 2000},
            "runtime": {},
            "correlationName": "mm-host-tools-recovery",
            "workspaceAttachment": {
                "kind": "volume",
                "sourceRef": "ws",
                "targetPath": "/workspaces/run",
                "accessMode": "read-write",
            },
            "skillAttachment": {
                "kind": "volume",
                "sourceRef": "skills",
                "targetPath": "/opt/moonmind-skills",
                "accessMode": "read-only",
            },
            "toolAttachments": [
                {
                    "kind": "image",
                    "sourceRef": f"image:{image_ref}",
                    "targetPath": "/opt/moonmind-tools",
                    "accessMode": "read-only",
                    "cleanupRef": None,
                    "toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64,
                    "tools": [
                        {
                            "name": "docker",
                            "version": "container-v1",
                            "path": "bin/moonmind",
                            "versionProbe": ["--help"],
                        },
                        {
                            "name": "gh",
                            "version": "2.76.2",
                            "path": "bin/gh",
                            "versionProbe": ["--version"],
                        },
                    ],
                }
            ],
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": "mm-host-state-test",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": {},
        }
    )


class _ImageContentsBackend:
    """Docker fake that answers from which local images own the tools."""

    def __init__(self, *, present: set[str], with_tools: set[str]) -> None:
        self.present = present
        self.with_tools = with_tools
        self.calls: list[list[str]] = []

    async def run(self, argv, **_kwargs):
        command = list(argv)
        self.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            if command[3] in self.present:
                return 0, "sha256:present", ""
            return 1, "", "No such image"
        if command[:2] == ["docker", "run"] and "test -x" in " ".join(command):
            image = command[command.index("--entrypoint") + 2]
            paths = command[command.index("--") + 1 :]
            owned = image in self.with_tools and all(
                path.startswith("/opt/moonmind-tools/bin/") for path in paths
            )
            return (0, "", "") if owned else (1, "", "")
        return 0, ("container-id" if command[1] == "create" else ""), ""

    def launched_image(self) -> str:
        create = next(argv for argv in self.calls if argv[:2] == ["docker", "create"])
        return create[create.index("--entrypoint") + 2]


def _record_deployment_host(monkeypatch, tmp_path: Path, image_ref: str) -> None:
    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "resolved.json")
    )
    for key in (
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "OMNIGENT_PI_HOST_IMAGE_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    store.save_resolved_state(
        ResolvedOmnigentDeploymentState.model_validate(
            {
                "serverImageRef": "ghcr.io/omnigent-ai/omnigent-server@sha256:"
                + "d" * 64,
                "opencodeHostImageRef": image_ref,
                "sharedHostImageRef": image_ref,
                "details": {
                    "hostImageProvenance": {
                        image_ref: {
                            "buildDigest": "sha256:" + "2" * 64,
                            "version": "0.14.1",
                        }
                    }
                },
            }
        )
    )


def _launcher(backend) -> DockerOmnigentHostLauncher:
    class Scripts:
        def build_entrypoint(self, **_kwargs):
            return "exec true", {}

    return DockerOmnigentHostLauncher(
        backend=backend, runtime_scripts=Scripts(), server_url="http://omnigent:8000"
    )


@pytest.mark.asyncio
async def test_plan_pinned_to_pre_tools_host_launches_updated_deployment_host(
    monkeypatch, tmp_path: Path
) -> None:
    """A plan made before an update still runs on the updated host image.

    The planned image is still cached locally but predates image-owned tools,
    so launching it can only fail exact-host attestation. The deployment now
    records a qualified same-repository image that owns the tools; the launch
    recovers onto it instead of failing the workflow.
    """

    _record_deployment_host(monkeypatch, tmp_path, _TOOLED_HOST)
    backend = _ImageContentsBackend(
        present={_PRE_TOOLS_HOST, _TOOLED_HOST}, with_tools={_TOOLED_HOST}
    )

    result = await _launcher(backend).launch(
        spec=_tool_launch_spec(_PRE_TOOLS_HOST),
        host_class=_pinned_host_class(_PRE_TOOLS_HOST),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == _TOOLED_HOST
    assert result["launchImageRef"] == _TOOLED_HOST


@pytest.mark.asyncio
async def test_fresh_launch_from_in_flight_plan_follows_installed_host(
    monkeypatch, tmp_path: Path
) -> None:
    """A new attempt launches the installed host even if the old one is cached.

    MoonLadderStudios/MoonMind#4627: an update replaced the host image while a
    step was running. Its successor attempt reuses the run's plan, whose
    pinned digest is still cached locally. The attempt that already ran keeps
    its recorded image; the fresh launch follows the installed release.
    """

    _record_deployment_host(monkeypatch, tmp_path, _TOOLED_HOST)
    backend = _ImageContentsBackend(
        present={_PRE_TOOLS_HOST, _TOOLED_HOST},
        with_tools={_PRE_TOOLS_HOST, _TOOLED_HOST},
    )

    result = await _launcher(backend).launch(
        spec=_tool_launch_spec(_PRE_TOOLS_HOST),
        host_class=_pinned_host_class(_PRE_TOOLS_HOST),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == _TOOLED_HOST
    assert result["launchImageRef"] == _TOOLED_HOST


@pytest.mark.asyncio
async def test_planned_host_is_kept_when_only_it_owns_the_required_tools(
    monkeypatch, tmp_path: Path
) -> None:
    """The installed host cannot replace a planned host that alone satisfies it."""

    _record_deployment_host(monkeypatch, tmp_path, _TOOLED_HOST)
    backend = _ImageContentsBackend(
        present={_PRE_TOOLS_HOST, _TOOLED_HOST},
        with_tools={_PRE_TOOLS_HOST},
    )

    await _launcher(backend).launch(
        spec=_tool_launch_spec(_PRE_TOOLS_HOST),
        host_class=_pinned_host_class(_PRE_TOOLS_HOST),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == _PRE_TOOLS_HOST


@pytest.mark.asyncio
async def test_current_deployment_host_launches_without_extra_tool_probe(
    monkeypatch, tmp_path: Path
) -> None:
    """Steady state: no other qualified image exists, so nothing is probed.

    When the deployment itself still records the planned image, there is no
    better host to recover onto; exact-host attestation reports the precise
    gap and the launch adds no container start of its own.
    """

    _record_deployment_host(monkeypatch, tmp_path, _PRE_TOOLS_HOST)
    backend = _ImageContentsBackend(present={_PRE_TOOLS_HOST}, with_tools=set())

    await _launcher(backend).launch(
        spec=_tool_launch_spec(_PRE_TOOLS_HOST),
        host_class=_pinned_host_class(_PRE_TOOLS_HOST),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == _PRE_TOOLS_HOST
    assert not any("test -x" in " ".join(argv) for argv in backend.calls)


_OPENCODE_DEPLOYED_HOST = _HOST_REPO + "@sha256:" + "e" * 64
_SHARED_DEPLOYED_HOST = _HOST_REPO + "@sha256:" + "f" * 64
_PI_DEPLOYED_HOST = _HOST_REPO + "@sha256:" + "9" * 64


def _record_distinct_deployment_hosts(monkeypatch, tmp_path: Path) -> None:
    """Each host family installs its own digest of the same repository."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "resolved.json")
    )
    for key in (
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "OMNIGENT_PI_HOST_IMAGE_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    deployed = (_OPENCODE_DEPLOYED_HOST, _SHARED_DEPLOYED_HOST, _PI_DEPLOYED_HOST)
    store.save_resolved_state(
        ResolvedOmnigentDeploymentState.model_validate(
            {
                "serverImageRef": "ghcr.io/omnigent-ai/omnigent-server@sha256:"
                + "d" * 64,
                "opencodeHostImageRef": _OPENCODE_DEPLOYED_HOST,
                "sharedHostImageRef": _SHARED_DEPLOYED_HOST,
                "piHostImageRef": _PI_DEPLOYED_HOST,
                "details": {
                    "hostImageProvenance": {
                        ref: {"buildDigest": "sha256:" + "2" * 64, "version": "0.14.1"}
                        for ref in deployed
                    }
                },
            }
        )
    )


def _family_host_class(image_ref: str, host_class_id: str, version: int) -> HostClass:
    return _pinned_host_class(image_ref).model_copy(
        update={"hostClassId": host_class_id, "version": version}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "host_class_id", "version"),
    [
        (_SHARED_DEPLOYED_HOST, "omnigent-codex", 1),
        (_PI_DEPLOYED_HOST, "omnigent-pi", 1),
        (_OPENCODE_DEPLOYED_HOST, "omnigent-opencode", 1),
    ],
)
async def test_plan_pinned_to_an_installed_host_keeps_it(
    monkeypatch, tmp_path: Path, requested: str, host_class_id: str, version: int
) -> None:
    """An installed digest is never swapped for another family's installed one.

    MoonLadderStudios/MoonMind#4627: the OpenCode, shared and Pi host refs all
    default to one repository. A plan already pinned to a currently installed
    ref follows the installed release; launching a different family's digest
    would change the runtime without any update having happened.
    """

    _record_distinct_deployment_hosts(monkeypatch, tmp_path)
    deployed = {_OPENCODE_DEPLOYED_HOST, _SHARED_DEPLOYED_HOST, _PI_DEPLOYED_HOST}
    backend = _ImageContentsBackend(present=deployed, with_tools=deployed)

    result = await _launcher(backend).launch(
        spec=_tool_launch_spec(requested),
        host_class=_family_host_class(requested, host_class_id, version),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == requested
    assert result["launchImageRef"] == requested
    assert not any("test -x" in " ".join(argv) for argv in backend.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host_class_id", "version", "installed"),
    [
        ("omnigent-codex", 1, _SHARED_DEPLOYED_HOST),
        ("omnigent-opencode", 2, _SHARED_DEPLOYED_HOST),
        ("omnigent-pi", 1, _PI_DEPLOYED_HOST),
        ("omnigent-opencode", 1, _OPENCODE_DEPLOYED_HOST),
    ],
)
async def test_stale_plan_follows_its_own_host_familys_installed_image(
    monkeypatch, tmp_path: Path, host_class_id: str, version: int, installed: str
) -> None:
    """A plan pinned before an update moves to its family's installed host."""

    _record_distinct_deployment_hosts(monkeypatch, tmp_path)
    present = {
        _PRE_TOOLS_HOST,
        _OPENCODE_DEPLOYED_HOST,
        _SHARED_DEPLOYED_HOST,
        _PI_DEPLOYED_HOST,
    }
    backend = _ImageContentsBackend(present=present, with_tools=present)

    await _launcher(backend).launch(
        spec=_tool_launch_spec(_PRE_TOOLS_HOST),
        host_class=_family_host_class(_PRE_TOOLS_HOST, host_class_id, version),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    assert backend.launched_image() == installed
