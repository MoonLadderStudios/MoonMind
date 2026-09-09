"""Locked portable CLI -> tool projection -> exact-host attestation replay."""

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

pytestmark = [pytest.mark.asyncio, pytest.mark.reliability_journey]

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "harness", ["opencode-native", "codex-native", "claude-native"]
)
@pytest.mark.parametrize("fault", [None, "digest", "wrong_probe", "missing_probe"])
async def test_declared_probe_reaches_exact_host_and_keeps_digest_gate(
    tmp_path, monkeypatch, harness, fault
):
    # Escaped incident: mm:12352fc2-44cb-4f82-8dbd-9283d0fa8f63,
    # retry 22132d0b-ba97-4e69-8b56-a8af27f0f37a. The deployed lock
    # declares --help, but attestation hardcoded --version and failed before
    # starting the session. Execute the real portable CLI and shell probe.
    bundle = tmp_path / "mounted tools"
    (bundle / "bin").mkdir(parents=True)
    executable = bundle / "bin/moonmind"
    shutil.copyfile(
        ROOT / "services/omnigent/scripts/moonmind-container-cli.py", executable
    )
    executable.chmod(0o555)
    skills = tmp_path / "skills"
    skills.mkdir()
    image_ref = "test-host@sha256:" + "a" * 64
    build_digest = "sha256:" + "b" * 64
    probe_calls = []

    class Backend(DockerCommandBackend):
        async def inspect_container(self, name):
            return {
                "Id": "host-container",
                "Config": {"Image": image_ref, "Labels": {}},
                "Mounts": [
                    {
                        "Source": str(tmp_path),
                        "Destination": "/workspaces/run",
                        "RW": True,
                    },
                    {"Source": str(skills), "Destination": str(skills), "RW": False},
                    {"Name": "test-tools", "Destination": str(bundle), "RW": False},
                ],
            }

        async def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "volume", "inspect"]:
                return 0, "[]", ""
            if argv[:3] == ["docker", "image", "inspect"]:
                return 0, json.dumps([{
                    "Config": {
                        "Labels": {"moonmind.omnigent.build_digest": build_digest}
                    },
                    "RepoDigests": [image_ref],
                    "Os": "linux",
                    "Architecture": "amd64",
                }]), ""
            assert argv[:3] == ["docker", "exec", "host-container"]
            if argv[3] == "/opt/venv/bin/omnigent":
                return 0, "omnigent 1.18.11", ""
            if argv[3] == "/opt/venv/bin/python":
                # Runner/skill environment checks are independent of the
                # changed mounted-tool projection and command boundary.
                return 0, "", ""
            assert argv[3] == "/bin/sh"
            if str(executable) in argv:
                probe_calls.append(argv)
            return await super().run(argv[3:], **kwargs)

    backend = Backend()
    service = OmnigentMountedToolService(
        backend=backend,
        manifest_path=ROOT / "services/omnigent/tools/manifest.lock.json",
        volume_ref="test-tools",
    )
    attachments = await service.materialize({
        "toolDeliveryRef": "tool-delivery:sha256:" + "c" * 64,
        "tools": ["docker"],
    })
    attachments[0]["targetPath"] = str(bundle)
    tool = attachments[0]["tools"][0]
    if fault == "digest":
        tool["executableDigests"] = ["0" * 64]
    elif fault == "wrong_probe":
        tool["versionProbe"] = ["--version"]
    elif fault == "missing_probe":
        tool.pop("versionProbe", None)
    spec = SimpleNamespace(
        labels={},
        workspaceAttachment={
            "sourceRef": str(tmp_path), "targetPath": "/workspaces/run",
            "accessMode": "read-write",
        },
        skillAttachment={"sourceRef": str(skills), "targetPath": str(skills)},
        stepExecutionId="step-1",
        toolAttachments=attachments,
        controlAttachment=None,
        githubCredentialAttachment=None,
    )
    model = "provider/model"
    plan = SimpleNamespace(payload=SimpleNamespace(
        harnessId=harness, harnessImplementationRef="impl-1",
        modelConfig=SimpleNamespace(qualifiedId=model),
    ))
    host_class = SimpleNamespace(
        imageRef=image_ref, omnigentBuildDigest=build_digest,
        architectures=["linux/amd64"], omnigentVersion="1.18.11",
        declaredHarnessImplementations=[SimpleNamespace(
            harnessId=harness, implementationRef="impl-1", runtimeDependencies=[],
        )],
    )
    artifacts = SimpleNamespace(
        write_json=AsyncMock(side_effect=["artifact:host", "artifact:models"])
    )
    monkeypatch.setattr(
        attestation, "attest_docker_workload_egress", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        attestation,
        "_read_exact_host_model_options",
        AsyncMock(return_value=([model], "test")),
    )
    egress = {
        "profileRef": "test", "profileDigest": build_digest,
        "enforcerImplementation": "test", "backendRef": "test",
        "networkRef": "test", "gatewayRef": "test",
        "appliedRuleDigest": build_digest, "configDigest": build_digest,
        "gatewayImageDigest": build_digest, "validatedAt": "2026-09-09T00:00:00Z",
        "attestationRef": "artifact:egress",
    }
    attestor = attestation.DockerOmnigentHostAttestor(
        backend=backend, client=None, artifacts=artifacts
    )
    arguments = dict(
        request=SimpleNamespace(), plan=plan, spec=spec, host_class=host_class,
        launch_result={"containerName": "host-container"},
        registration={"omnigentHostId": "host-1", "host": {}, "harnessReady": True},
        credential_handles=[], egress_attestation=egress,
    )
    if fault:
        with pytest.raises(HarnessPlatformError):
            await attestor.attest(**arguments)
        artifacts.write_json.assert_not_awaited()
    else:
        result = await attestor.attest(**arguments)
        assert result["hostHarnessAttestationRef"] == "artifact:host"
        assert len(probe_calls) == 1
        assert probe_calls[0][-1] == "--help"
        evidence = artifacts.write_json.await_args_list[0].kwargs["payload"]
        assert evidence["toolMounts"][0]["digestVerified"] is True
        assert "usage: moonmind" in evidence["toolMounts"][0]["probe"]
