"""Replay cold-host catalog loss through launch, attestation and cleanup."""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import sys
import time
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    remediation_for,
)
from moonmind.omnigent.host_runtime import (
    GenericOmnigentHostRuntime,
    PreparedHostInputs,
)
from moonmind.omnigent.host_services import attestation
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)
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
        "if fault == 'model_mismatch_probe_error' and attempt > 1:\n"
        "    sys.stderr.write('private provider diagnostic')\n"
        "    sys.exit(1)\n"
        "if fault.startswith('model_'):\n"
        "    sys.stdout.write('opencode-go/gemini-4-argon-contributor\\n' "
        "+ 'opencode/gemini-4-argon-contributor-free\\n')\n"
        "    sys.exit(0)\n"
        f"catalogs = {catalogs!r}\n"
        "sys.stderr.write(catalogs['stderr'])\n"
        "sys.stdout.write(catalogs['bundled'] if fault in {'exhausted', 'artifact_error'} or "
        "(fault == 'transient' and attempt == 1) else catalogs['refreshed'])\n"
    )
    cli.chmod(0o755)


@pytest.mark.parametrize(
    ("fault", "harness_id"),
    list(
        product(
            [
                "none",
                "transient",
                "exhausted",
                "probe_error",
                "build_mismatch",
                "cancelled",
                "artifact_error",
            ],
            ["opencode-native", "codex-native", "claude-native", "pi-native"],
        )
    )
    + list(
        product(
            [
                "transport_error",
                "rate_limit",
                "request_timeout",
                "probe_exhausted",
                "http_400",
                "http_401",
                "http_403",
                "http_404",
                "http_409",
                "http_422",
            ],
            ["codex-native", "claude-native", "pi-native"],
        )
    )
    # The actual failure used a different ID from the host's published model.
    # A corrected explicit selection is served; a nearby name is never an alias.
    + [
        (fault, "opencode-native")
        for fault in (
            "model_mismatch",
            "model_mismatch_probe_error",
            "model_corrected",
        )
    ]
    # A changed vendor release is provenance; an absent runtime is not.
    + [
        (fault, "opencode-native")
        for fault in ("vendor_release_changed", "vendor_runtime_missing")
    ]
    + [
        (fault, "codex-native")
        for fault in (
            "oauth_ended",
            "oauth_ended_during_refresh",
            "oauth_ended_bundled_match",
            "oauth_ended_probe_error",
            "oauth_aged",
            "oauth_aged_transient",
            "oauth_reconnected",
        )
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
    record_ended_login = AsyncMock()
    monkeypatch.setattr(
        attestation, "record_ended_codex_login", record_ended_login, raising=False
    )
    credential_handles = []
    credential_home = tmp_path / "codex-home"

    def write_login(*, issued_at: int, expires_at: int) -> None:
        claims = (
            base64.urlsafe_b64encode(
                json.dumps({"iat": issued_at, "exp": expires_at}).encode()
            )
            .decode()
            .rstrip("=")
        )
        (credential_home / "auth.json").write_text(
            json.dumps(
                {"tokens": {"access_token": f"header.{claims}.private-token-material"}}
            )
        )

    if fault.startswith("oauth_"):
        selected_model = "gpt-6.1-sol"
        credential_home.mkdir()
        now = int(time.time())
        issued_at = now if fault == "oauth_reconnected" else now - 86400
        expires_at = issued_at if fault == "oauth_ended" else issued_at + 3600
        write_login(issued_at=issued_at, expires_at=expires_at)
        credential_handles = [
            {
                "credentialRuntimeRef": "credential-runtime:original",
                "credentialGeneration": 4,
                "providerProfileRef": "codex_openai_oauth",
                "materializerRef": "codex-oauth-home@1",
                "attachments": [
                    {
                        "kind": "volume",
                        "sourceRef": "codex-auth",
                        "targetPath": "/home/app/.codex",
                        "accessMode": "read-write",
                    }
                ],
            }
        ]
    if fault.startswith("model_"):
        selected_model = "opencode-go/gemini-4-argon"
        if fault == "model_corrected":
            selected_model += "-contributor"
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
            if argv[:3] == ["docker", "container", "inspect"]:
                return 0, "rw", ""
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
            if argv[3:] == ["opencode", "--version"]:
                events.append("vendor_version")
                if fault == "vendor_runtime_missing":
                    # The real backend turns the failed exec into its typed cause.
                    return await super().run(["sh", "-c", "exit 127"], **kwargs)
                return 0, "opencode 2.0.1", ""
            if argv[3] == "/bin/sh":
                if argv[-1] == "/home/app/.codex":
                    return 0, "4", ""
                return 0, "", ""
            if argv[3:] == ["codex", "login", "status"]:
                events.append("login_status")
                return 0, "Logged in using ChatGPT", ""
            assert argv[3:5] == ["/opt/venv/bin/python", "-c"]
            if argv[-1] == "/home/app/.codex":
                events.append("login_session")
                return await super().run(
                    [sys.executable, *argv[4:-1], str(credential_home)], **kwargs
                )
            attempts.append(argv[2])
            events.append("catalog")
            if fault == "cancelled":
                raise asyncio.CancelledError()
            # Real command runner, probe, upstream parser, and CLI subprocess.
            # Substitute only Docker transport and isolated package location.
            return await super().run(
                ["env", f"PYTHONPATH={tmp_path}", sys.executable, *argv[4:]], **kwargs
            )

    def catalog_response(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == (
            f"/v1/hosts/replay-host/harnesses/{harness_id}/model-options"
        )
        attempts.append("replay-host")
        events.append("catalog")
        if fault.startswith("oauth_ended_"):
            # A refresh request can be the first proof that the session ended.
            now = int(time.time())
            write_login(issued_at=now, expires_at=now)
            if fault == "oauth_ended_probe_error":
                return httpx.Response(503, text="private provider diagnostic")
        if fault == "cancelled":
            raise asyncio.CancelledError()
        if fault == "build_mismatch":
            raise HarnessPlatformError(
                "host mismatch", code="OMNIGENT_HARNESS_BUILD_MISMATCH"
            )
        if fault.startswith("http_"):
            return httpx.Response(int(fault.removeprefix("http_")))
        if fault == "probe_exhausted":
            return httpx.Response(503, text="private provider diagnostic")
        if len(attempts) == 1:
            if fault == "transport_error":
                raise httpx.ReadTimeout("private provider diagnostic", request=request)
            transient_status = {
                "probe_error": 502,
                "rate_limit": 429,
                "request_timeout": 408,
            }.get(fault)
            if transient_status is not None:
                return httpx.Response(
                    transient_status, text="private provider diagnostic"
                )
        available = fault not in {
            "exhausted",
            "artifact_error",
            "oauth_ended",
            "oauth_ended_during_refresh",
        } and (fault not in {"transient", "oauth_aged_transient"} or len(attempts) > 1)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"id": selected_model if available else "opencode-go/older-model"}
                ]
            },
        )

    client = OmnigentHttpClient(
        base_url="https://replay.invalid",
        transport=httpx.MockTransport(catalog_response),
    )

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
            if fault in {
                "exhausted",
                "probe_exhausted",
                "model_mismatch",
                "model_mismatch_probe_error",
            }:
                assert (tmp_path / "generic-host-model-options.json").exists()
            return {"containerRemoved": True}

    monkeypatch.setattr(
        attestation.upstream_probes,
        "run_runner_command",
        AsyncMock(return_value=(0, "", "")),
    )
    monkeypatch.setattr(
        attestation.upstream_probes,
        "run_opencode_shell_command",
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
                # The support evidence recorded this release; the vendor
                # faults launch a host whose runtime differs from it.
                runtimeDependencies=(
                    [{"name": "opencode", "version": "1.18.11"}]
                    if fault.startswith("vendor_")
                    else []
                ),
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
            backend=Backend(), client=client, artifacts=Artifacts()
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
    realize_inputs = dict(
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
        credential_handles=credential_handles,
    )
    if fault.startswith("oauth_ended"):
        # The stored login passes `codex login status`, but cannot refresh.
        # Its bundled catalog must never become a false model-unavailable error.
        with pytest.raises(HarnessPlatformError) as exc:
            await runtime.realize(**realize_inputs)
        assert exc.value.code == "OMNIGENT_HOST_HARNESS_NEEDS_AUTH"
        assert remediation_for(exc.value.code) == "reauthenticate"
        assert "codex_openai_oauth" in str(exc.value)
        assert "ended" in str(exc.value)
        assert "private-token-material" not in str(exc.value)
        record_ended_login.assert_awaited_once_with("codex_openai_oauth", 4)
        assert attempts == ([] if fault == "oauth_ended" else ["replay-host"])
        assert events.count("launch") == 1
        assert events.count("login_status") == 1
        assert events.count("login_session") == (1 if fault == "oauth_ended" else 2)
        if fault != "oauth_ended":
            assert events.index("login_session") < events.index("catalog")
            assert events[-2] == "login_session"
        assert events.count("cleanup") == 1
        assert events[-1] == "cleanup"
        assert candidate.read_text() == "preserved edits"
        assert plan.payload.modelConfig.qualifiedId == selected_model
        return
    if fault == "vendor_runtime_missing":
        with pytest.raises(HarnessPlatformError) as exc:
            await runtime.realize(**realize_inputs)
        assert exc.value.code == "OMNIGENT_VENDOR_RUNTIME_MISMATCH"
        assert remediation_for(exc.value.code) == "align_vendor_runtime"
        assert attempts == []
        assert events.count("launch") == 1
        assert events[-1] == "cleanup"
        assert candidate.read_text() == "preserved edits"
        return
    catalog_exhausted = fault in {
        "exhausted",
        "artifact_error",
        "probe_exhausted",
        "model_mismatch",
        "model_mismatch_probe_error",
    }
    terminal_http_error = fault.startswith("http_")
    if (
        catalog_exhausted
        or terminal_http_error
        or fault in {"build_mismatch", "cancelled"}
    ):
        error = (
            OmnigentClientError
            if terminal_http_error
            else (
                asyncio.CancelledError if fault == "cancelled" else HarnessPlatformError
            )
        )
        with pytest.raises(error) as exc:
            await runtime.realize(**realize_inputs)
        assert events[-1] == "cleanup"
        if terminal_http_error:
            assert exc.value.status_code == int(fault.removeprefix("http_"))
        elif fault != "cancelled":
            # Reads that never completed observed no absence: the cause stays
            # an unavailable catalog, not a request to pick another model.
            assert exc.value.code == (
                "OMNIGENT_HARNESS_BUILD_MISMATCH"
                if not catalog_exhausted
                else (
                    "OMNIGENT_HARNESS_CATALOG_UNAVAILABLE"
                    if fault == "probe_exhausted"
                    else "OMNIGENT_MODEL_UNAVAILABLE"
                )
            )
            assert remediation_for(exc.value.code) == (
                "retry_catalog_sync"
                if fault == "probe_exhausted"
                else (
                    "select_available_model"
                    if catalog_exhausted
                    else "align_host_build"
                )
            )
        assert len(attempts) == (manifest["maxAttempts"] if catalog_exhausted else 1)
        if fault == "probe_exhausted":
            assert "could not be read on the exact host in 3 attempts" in str(exc.value)
            assert exc.value.transient is True
        elif catalog_exhausted:
            assert "after 3 catalog reads" in str(exc.value)
        if fault.startswith("model_mismatch"):
            assert "opencode-go/gemini-4-argon-contributor" in str(exc.value)
            assert "opencode/gemini-4-argon-contributor-free" not in str(exc.value)
            assert "Select an exact model ID" in str(exc.value)
            assert "private provider diagnostic" not in str(exc.value)
    else:
        result = await runtime.realize(**realize_inputs)
        assert (
            result["modelOptionAttestationRef"]
            == "artifact:generic-host-model-options.json"
        )
        assert "cleanup" not in events
        assert len(attempts) == (
            1
            if fault
            in {
                "none",
                "model_corrected",
                "oauth_aged",
                "oauth_reconnected",
                "vendor_release_changed",
            }
            else 2
        )
        if fault.startswith("oauth_"):
            assert events.index("login_session") < events.index("catalog")
            assert events.count("login_session") == len(attempts) + 1
        if fault == "vendor_release_changed":
            host_evidence = json.loads(
                (tmp_path / "generic-host-attestation.json").read_text()
            )
            # Observed and declared releases are both kept for diagnosis.
            assert host_evidence["runtimeVersions"] == {"opencode": "opencode 2.0.1"}
            assert host_evidence["declaredRuntimeVersions"] == {"opencode": "1.18.11"}
    record_ended_login.assert_not_awaited()
    assert events.count("launch") == 1
    assert set(attempts) == {"replay-host"}
    assert candidate.read_text() == "preserved edits"
    assert plan.payload.modelConfig.qualifiedId == selected_model
    if not terminal_http_error and fault not in {
        "build_mismatch",
        "cancelled",
        "artifact_error",
    }:
        evidence = json.loads(
            (tmp_path / "generic-host-model-options.json").read_text()
        )
        assert evidence["selectedModel"] == selected_model
        assert len(evidence["attempts"]) == len(attempts)
        assert evidence["attempts"][-1]["selectedModelPresent"] is (
            not catalog_exhausted
        )
        if fault in {
            "probe_error",
            "transport_error",
            "rate_limit",
            "request_timeout",
            "probe_exhausted",
        }:
            assert (
                evidence["attempts"][0]["errorCode"]
                == "OMNIGENT_HARNESS_CATALOG_UNAVAILABLE"
            )
        if fault == "model_mismatch_probe_error":
            assert (
                evidence["attempts"][-1]["errorCode"]
                == "OMNIGENT_HARNESS_CATALOG_UNAVAILABLE"
            )
        assert "private provider diagnostic" not in json.dumps(evidence)
