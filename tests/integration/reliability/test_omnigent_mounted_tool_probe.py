"""Replay mounted-tool delivery through host attestation and the real CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.host_services import attestation
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.mounted_tools import OmnigentMountedToolService
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from tests.integration.reliability.helpers import load_replay

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@pytest.mark.parametrize(
    "harness_id", ["opencode-native", "codex-native", "claude-native"]
)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "digest",
        "probe",
        "writable_mount",
        "missing_probe",
        "null_probe",
        "empty_probe",
        "string_probe",
        "null_argument",
        "empty_argument",
        "nul_argument",
    ],
)
async def test_declared_tool_probe_reaches_exact_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness_id: str,
    fault: str | None,
) -> None:
    manifest = load_replay("omnigent-mounted-tool-probe", "manifest.json")
    expected = load_replay("omnigent-mounted-tool-probe", "expected-outcome.json")
    repo_root = Path(__file__).resolve().parents[3]
    bundle = tmp_path / "mounted tools"
    (bundle / "bin").mkdir(parents=True)
    executable = bundle / "bin/moonmind"
    shutil.copyfile(
        repo_root / "services/omnigent/scripts/moonmind-container-cli.py",
        executable,
    )
    executable.chmod(0o555)
    probes = []
    spec = SimpleNamespace(
        labels={},
        stepExecutionId="replay-step",
        workspaceAttachment={
            "sourceRef": "workspace",
            "targetPath": "/workspace",
            "accessMode": "read-write",
        },
        skillAttachment={"sourceRef": "skills", "targetPath": "/skills"},
        controlAttachment=None,
        githubCredentialAttachment=None,
    )
    image_ref = "example/host@sha256:" + "1" * 64
    build_digest = "sha256:" + "2" * 64

    class Backend(DockerCommandBackend):
        async def inspect_container(self, _name):
            return {
                "Config": {"Labels": {}, "Image": image_ref},
                "Mounts": [
                    {"Name": "workspace", "Destination": "/workspace", "RW": True},
                    {"Name": "skills", "Destination": "/skills", "RW": False},
                    {
                        "Name": "replay-tools",
                        "Destination": str(bundle),
                        "RW": fault == "writable_mount",
                    },
                ],
            }

        async def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "volume", "inspect"]:
                return 0, "[]", ""
            if argv[:3] == ["docker", "image", "inspect"]:
                return (
                    0,
                    json.dumps(
                        [
                            {
                                "Config": {
                                    "Labels": {
                                        "moonmind.omnigent.build_digest": build_digest
                                    }
                                },
                                "RepoDigests": [image_ref],
                                "Os": "linux",
                                "Architecture": "amd64",
                            }
                        ]
                    ),
                    "",
                )
            if argv[3:] == ["/opt/venv/bin/omnigent", "--version"]:
                return 0, "omnigent 1.0", ""
            if argv[3:6] == ["/bin/sh", "-ceu", 'test -d "$1"; test -r "$1"']:
                return 0, "", ""
            if argv[:2] == ["docker", "exec"] and "sha256sum" in argv[5]:
                probes.append((argv, kwargs))
                # Substitute only Docker transport: execute the production shell
                # command, digest check, and real mounted CLI in a local process.
                return await super().run(argv[3:], **kwargs)
            raise AssertionError(f"unexpected attestation command: {argv}")

    backend = Backend()
    service = OmnigentMountedToolService(backend=backend, volume_ref="replay-tools")
    spec.toolAttachments = await service.materialize(
        {
            "tools": manifest["requiredTools"],
            "toolDeliveryRef": "tool-delivery:sha256:" + "3" * 64,
        }
    )
    attachment = spec.toolAttachments[0]
    attachment["targetPath"] = str(bundle)
    tool = attachment["tools"][0]
    assert tool["versionProbe"] == expected["versionProbe"]
    if fault == "digest":
        tool["executableDigests"] = ["0" * 64]
    elif fault == "probe":
        tool["versionProbe"] = ["--version"]
        code, _out, _err = await DockerCommandBackend().run(
            [str(executable), "--version"],
            check=False,
        )
        assert code == expected["probeFailureExitCode"]
    elif fault == "missing_probe":
        # Historical launch attachments can predate manifest probe projection.
        tool.pop("versionProbe")
    elif fault in {
        "null_probe",
        "empty_probe",
        "string_probe",
        "null_argument",
        "empty_argument",
        "nul_argument",
    }:
        tool["versionProbe"] = {
            "null_probe": None,
            "empty_probe": [],
            "string_probe": "--help",
            "null_argument": [None],
            "empty_argument": [""],
            "nul_argument": ["--help\x00"],
        }[fault]

    # Other attestation services are independent of mounted tools. Preserve the
    # full attestor ordering and evidence publication while isolating those seams.
    monkeypatch.setattr(
        attestation,
        "_run_exact_host_runner_command",
        AsyncMock(return_value=(0, "", "")),
    )
    monkeypatch.setattr(
        attestation,
        "_run_exact_host_opencode_command",
        AsyncMock(return_value=(0, "", "")),
    )
    monkeypatch.setattr(
        attestation, "attest_docker_workload_egress", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        attestation,
        "_read_exact_host_model_options",
        AsyncMock(
            return_value=(
                {"models": [{"id": "test/model"}]},
                "test",
            )
        ),
    )
    artifacts = SimpleNamespace(
        write_json=AsyncMock(side_effect=["artifact:host", "artifact:models"])
    )
    host_attestor = attestation.DockerOmnigentHostAttestor(
        backend=backend,
        client=SimpleNamespace(),
        artifacts=artifacts,
    )
    arguments = dict(
        request=SimpleNamespace(),
        plan=SimpleNamespace(
            payload=SimpleNamespace(
                harnessId=harness_id,
                harnessImplementationRef="test@1",
                modelConfig=SimpleNamespace(qualifiedId="test/model"),
            )
        ),
        spec=spec,
        host_class=SimpleNamespace(
            imageRef=image_ref,
            omnigentBuildDigest=build_digest,
            architectures=["linux/amd64"],
            omnigentVersion="1.0",
            declaredHarnessImplementations=[
                SimpleNamespace(
                    harnessId=harness_id,
                    implementationRef="test@1",
                    runtimeDependencies=[],
                )
            ],
        ),
        launch_result={"containerName": "replay-host"},
        registration={
            "omnigentHostId": "replay-host",
            "host": {},
            "harnessReady": True,
        },
        credential_handles=[],
        egress_attestation={
            "profileRef": "test",
            "profileDigest": "test",
            "enforcerImplementation": "test",
            "backendRef": "test",
            "networkRef": "test",
            "gatewayRef": "test",
            "appliedRuleDigest": "test",
            "configDigest": "test",
            "gatewayImageDigest": "test",
            "validatedAt": "2026-09-09T05:41:20Z",
            "attestationRef": "artifact:egress",
        },
    )
    if fault:
        with pytest.raises(HarnessPlatformError) as exc:
            await host_attestor.attest(**arguments)
        assert exc.value.code == expected["failureCode"]
        artifacts.write_json.assert_not_awaited()
    else:
        result = await host_attestor.attest(**arguments)
        assert result["hostHarnessAttestationRef"] == "artifact:host"
        evidence = artifacts.write_json.await_args_list[0].kwargs["payload"]
        assert evidence["toolMounts"][0]["digestVerified"] is True
        assert evidence["toolMounts"][0]["versionProbe"] == expected["versionProbe"]
    if fault not in {None, "digest", "probe"}:
        assert probes == []
    else:
        assert len(probes) == 1
        assert probes[0][1]["timeout_seconds"] == expected["probeTimeoutSeconds"]
