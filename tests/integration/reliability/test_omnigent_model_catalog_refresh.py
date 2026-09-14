"""Replay cold-host catalog loss through launch, attestation and cleanup."""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_runtime import (
    GenericOmnigentHostRuntime,
    PreparedHostInputs,
)
from moonmind.omnigent.host_services import attestation
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


def _install_catalog_cli(root: Path, *, fault: str) -> None:
    """Use the pinned portable parser with a scripted CLI transport, no network."""
    upstream = (
        Path(__file__).parent
        / "replays/omnigent-model-catalog-refresh/portable_catalog_helper.txt"
    )
    tree = ast.parse(upstream.read_text())
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "list_opencode_cli_model_options"
    )
    package = root / "omnigent/harnesses/opencode_native"
    package.mkdir(parents=True)
    for directory in (package, package.parent, package.parent.parent):
        (directory / "__init__.py").touch()
    cli = root / "opencode"
    (package / "app_server.py").write_text(
        "from __future__ import annotations\n"
        "import re, subprocess\n"
        f"def find_opencode_cli(path): return {str(cli)!r}\n"
        "_ANSI_RE = re.compile(r'\x1b\\[[0-9;]*m')\n" + ast.unparse(helper) + "\n"
    )
    catalogs = load_replay("omnigent-model-catalog-refresh", "catalogs.json")
    cli.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "assert sys.argv[1:] == ['models', '--refresh']\n"
        f"counter = pathlib.Path({str(root / 'attempts')!r})\n"
        "attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(attempt))\n"
        f"fault = {fault!r}\n"
        "if fault == 'probe_error' and attempt == 1:\n"
        "    sys.stderr.write('private provider diagnostic')\n"
        "    sys.exit(1)\n"
        f"catalogs = {catalogs!r}\n"
        "sys.stderr.write(catalogs['stderr'])\n"
        "sys.stdout.write(catalogs['bundled'] if fault in {'exhausted', 'artifact_error'} or "
        "(fault == 'transient' and attempt == 1) else catalogs['refreshed'])\n"
    )
    cli.chmod(0o755)


@pytest.mark.parametrize(
    "harness_id", ["opencode-native", "codex-native", "claude-native", "pi-native"]
)
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "transient",
        "exhausted",
        "probe_error",
        "build_mismatch",
        "cancelled",
        "artifact_error",
    ],
)
async def test_catalog_recovery_keeps_exact_host_until_evidence_or_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness_id: str, fault: str
) -> None:
    manifest = load_replay("omnigent-model-catalog-refresh", "manifest.json")
    _install_catalog_cli(tmp_path, fault=fault)
    if fault == "build_mismatch":
        (tmp_path / "omnigent/harnesses/opencode_native/app_server.py").write_text("")
    events = []
    attempts = []
    spec = None
    image_ref = "example/host@sha256:" + "1" * 64
    build_digest = "sha256:" + "2" * 64
    selected_model = manifest["selectedModel"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    candidate = workspace / "candidate.txt"
    candidate.write_text("preserved edits")

    class Backend(DockerCommandBackend):
        async def inspect_container(self, name):
            assert name == "replay-host"
            return {
                "Config": {"Labels": spec.labels, "Image": image_ref},
                "Mounts": [
                    {
                        "Source": str(workspace),
                        "Destination": "/workspaces/run",
                        "RW": True,
                    },
                    {"Name": "skills", "Destination": "/skills", "RW": False},
                ],
            }

        async def run(self, argv, **kwargs):
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
            assert argv[:3] == ["docker", "exec", "replay-host"]
            if argv[3:] == ["/opt/venv/bin/omnigent", "--version"]:
                return 0, "omnigent 0.13.0", ""
            if argv[3] == "/bin/sh":
                return 0, "", ""
            assert argv[3:5] == ["/opt/venv/bin/python", "-c"]
            attempts.append(argv[2])
            events.append("catalog")
            if fault == "cancelled":
                raise asyncio.CancelledError()
            # Real command runner, probe, upstream parser, and CLI subprocess.
            # Substitute only Docker transport and isolated package location.
            return await super().run(
                ["env", f"PYTHONPATH={tmp_path}", sys.executable, *argv[4:]], **kwargs
            )

    class Client:
        async def get_host_model_options(self, host_id, requested_harness):
            assert (host_id, requested_harness) == ("replay-host", harness_id)
            attempts.append(host_id)
            events.append("catalog")
            if fault == "cancelled":
                raise asyncio.CancelledError()
            if fault == "build_mismatch":
                raise HarnessPlatformError(
                    "host mismatch", code="OMNIGENT_HARNESS_BUILD_MISMATCH"
                )
            if fault == "probe_error" and len(attempts) == 1:
                raise HarnessPlatformError(
                    "catalog probe failed", code="OMNIGENT_MODEL_UNAVAILABLE"
                )
            available = fault not in {"exhausted", "artifact_error"} and (
                fault != "transient" or len(attempts) > 1
            )
            return {
                "models": [
                    {"id": selected_model if available else "opencode-go/older-model"}
                ]
            }

    class Artifacts:
        async def write_json(self, *, name, payload, **kwargs):
            events.append(name)
            if fault == "artifact_error":
                raise OSError("artifact store unavailable")
            path = tmp_path / name
            path.write_text(json.dumps(payload))
            return "artifact:" + name

    class Launcher:
        server_url = "http://replay.invalid"

        async def launch(self, **kwargs):
            nonlocal spec
            spec = kwargs["spec"]
            events.append("launch")
            return {
                "containerName": "replay-host",
                "stateVolumeRef": "state",
                "hostCleanupRef": "host-cleanup:replay",
                "stateCleanupRef": "state-cleanup:replay",
            }

    class Cleanup:
        async def cleanup(self, **kwargs):
            events.append("cleanup")
            assert kwargs["container_name"] == "replay-host"
            if fault == "exhausted":
                assert (tmp_path / "generic-host-model-options.json").exists()
            return {"containerRemoved": True}

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
    host_class = SimpleNamespace(
        ref="host@1",
        imageRef=image_ref,
        runtime={},
        omnigentBuildDigest=build_digest,
        omnigentVersion="0.13.0",
        architectures=["linux/amd64"],
        declaredHarnessImplementations=[
            SimpleNamespace(
                harnessId=harness_id,
                implementationRef="harness@1",
                runtimeDependencies=[],
            )
        ],
    )
    plan = SimpleNamespace(
        planRef="plan:original",
        payload=SimpleNamespace(
            hostClassRef="host@1",
            launchPolicyRef="policy@1",
            endpointRef="endpoint@1",
            harnessId=harness_id,
            harnessImplementationRef="harness@1",
            modelConfig=SimpleNamespace(qualifiedId=selected_model),
        ),
    )
    unused = SimpleNamespace()
    runtime = GenericOmnigentHostRuntime(
        launcher=Launcher(),
        workspace_service=unused,
        skill_service=unused,
        tool_service=unused,
        github_credential_service=unused,
        egress_service=unused,
        runtime_environment_service=SimpleNamespace(build=lambda **kwargs: {}),
        registration_waiter=SimpleNamespace(
            wait_for_registration=AsyncMock(
                return_value={
                    "omnigentHostId": "replay-host",
                    "host": {},
                    "harnessReady": True,
                }
            )
        ),
        host_attestor=attestation.DockerOmnigentHostAttestor(
            backend=Backend(), client=Client(), artifacts=Artifacts()
        ),
        cleanup_service=Cleanup(),
    )
    prepared = PreparedHostInputs(
        workspace_attachment={
            "sourceRef": str(workspace),
            "targetPath": "/workspaces/run",
            "accessMode": "read-write",
        },
        skill_attachment={
            "sourceRef": "skills",
            "targetPath": "/skills",
            "accessMode": "read-only",
        },
        tool_attachments=(),
        egress_attestation={
            "networkRef": "egress",
            "profileRef": "egress@1",
            "profileDigest": "test",
            "appliedRuleDigest": "test",
            "enforcerImplementation": "test",
            "backendRef": "test",
            "gatewayRef": "test",
            "gatewayImageDigest": "test",
            "configDigest": "test",
            "validatedAt": "2026-09-14T07:25:00Z",
            "attestationRef": "artifact:egress",
        },
    )
    operation = runtime.realize(
        request=AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="replay-run",
            idempotencyKey="replay-run",
        ),
        plan=plan,
        runtime_binding_id="binding:original",
        host_lease_ref="lease:original",
        host_lease_generation=1,
        host_class=host_class,
        launch_policy=SimpleNamespace(ref="policy@1", limits={}),
        prepared=prepared,
        credential_handles=[],
    )
    if fault in {"exhausted", "build_mismatch", "cancelled", "artifact_error"}:
        error = asyncio.CancelledError if fault == "cancelled" else HarnessPlatformError
        with pytest.raises(error) as exc:
            await operation
        assert events[-1] == "cleanup"
        if fault != "cancelled":
            assert exc.value.code == (
                "OMNIGENT_MODEL_UNAVAILABLE"
                if fault in {"exhausted", "artifact_error"}
                else "OMNIGENT_HARNESS_BUILD_MISMATCH"
            )
        assert len(attempts) == (
            manifest["maxAttempts"] if fault in {"exhausted", "artifact_error"} else 1
        )
        if fault in {"exhausted", "artifact_error"}:
            assert "after 3 catalog reads" in str(exc.value)
    else:
        result = await operation
        assert (
            result["modelOptionAttestationRef"]
            == "artifact:generic-host-model-options.json"
        )
        assert "cleanup" not in events
        assert len(attempts) == (1 if fault == "none" else 2)
    assert events.count("launch") == 1
    assert set(attempts) == {"replay-host"}
    assert candidate.read_text() == "preserved edits"
    assert plan.payload.modelConfig.qualifiedId == selected_model
    if fault not in {"build_mismatch", "cancelled", "artifact_error"}:
        evidence = json.loads(
            (tmp_path / "generic-host-model-options.json").read_text()
        )
        assert evidence["selectedModel"] == selected_model
        assert len(evidence["attempts"]) == len(attempts)
        assert evidence["attempts"][-1]["selectedModelPresent"] is (
            fault != "exhausted"
        )
        assert "private provider diagnostic" not in json.dumps(evidence)
