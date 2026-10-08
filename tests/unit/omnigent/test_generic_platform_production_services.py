from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.auth.bound_acquisition import EphemeralCredential
from moonmind.omnigent.bootstrap import store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.credential_materializers import (
    CredentialMaterializationContext,
    CredentialRuntimeHandle,
    DockerOmnigentProviderConfigMaterializer,
    DockerOpencodeAuthJsonMaterializer,
    credential_runtime_identity,
)
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    HarnessRecord,
    TrustState,
)
from moonmind.omnigent.harness_platform.catalog_service import (
    InMemoryHarnessCatalogRepository,
    OmnigentHarnessCatalogService,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
    remediation_for,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
    OmnigentPlannedHostResolver,
    _canonical_json_bytes,
    _ref,
)
from moonmind.omnigent.harness_platform.stores import (
    ExecutionPlanUsageIdentity,
    InMemoryExecutionPlanStore,
    InMemoryExecutionPlanUsageStore,
)
from moonmind.omnigent.host_image_drift import compatible_deployed_fallback
from moonmind.omnigent.host_leases import InMemoryOmnigentHostLeaseRepository
from moonmind.omnigent.host_ports import HostLaunchSpec, expected_omnigent_host_id
from moonmind.omnigent.host_services.attestation import (
    DockerOmnigentHostAttestor,
    _assert_exact_omnigent_build,
    _attest_workspace_mount,
    _read_exact_host_model_options,
    _run_exact_host_opencode_command,
    _run_exact_host_runner_command,
    _skill_projection_probe_message,
)
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
    github_repository_from_request,
)
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.mounted_tools import (
    OmnigentMountedToolService,
    deployment_mounted_tool_names,
)
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.omnigent.host_services.skills import OmnigentSkillDeliveryService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.provider_leases import (
    AcquiredProviderLease,
    OmnigentProviderLeaseCoordinator,
)
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingState,
)
from moonmind.omnigent.secret_resolution import (
    OmnigentSecretResolutionService,
    ScopedSecretBundle,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE
from moonmind.security.execution_fanout_capabilities import (
    verify_execution_fanout_capability,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def test_exact_host_attestation_requires_catalog_build_label() -> None:
    expected = "sha256:" + "a" * 64
    _assert_exact_omnigent_build(
        {"Config": {"Labels": {"moonmind.omnigent.build_digest": expected}}},
        expected,
    )

    with pytest.raises(HarnessPlatformError) as exc:
        _assert_exact_omnigent_build(
            {"Config": {"Labels": {"moonmind.omnigent.build_digest": ""}}},
            expected,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


def test_exact_host_attestation_enforces_workspace_access_mode() -> None:
    attachment = {
        "sourceRef": "/daemon/workspace/run-1",
        "targetPath": "/workspaces/run",
        "accessMode": "read-only",
    }
    evidence = _attest_workspace_mount(
        [
            {
                "Source": "/daemon/workspace/run-1",
                "Destination": "/workspaces/run",
                "RW": False,
            }
        ],
        attachment,
    )
    assert evidence["accessMode"] == "read-only"

    with pytest.raises(HarnessPlatformError):
        _attest_workspace_mount(
            [
                {
                    "Source": "/daemon/workspace/run-1",
                    "Destination": "/workspaces/run",
                    "RW": True,
                }
            ],
            attachment,
        )


@pytest.mark.asyncio
async def test_opencode_exact_host_model_options_use_portable_cli_helper() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            return (
                0,
                json.dumps(
                    {
                        "models": [
                            {
                                "id": "opencode-go/muse-spark-1.2-contributor",
                                "providerID": "opencode-go",
                            }
                        ]
                    }
                ),
                "",
            )

    class Client:
        async def get_host_model_options(self, *_args):
            raise AssertionError("OpenCode must not use the unsupported host tunnel")

    result, source = await _read_exact_host_model_options(
        backend=Backend(),
        client=Client(),
        container_name="mm-host-opencode",
        omnigent_host_id="host-opencode",
        harness_id="opencode-native",
    )

    assert result["models"][0]["id"] == "opencode-go/muse-spark-1.2-contributor"
    assert source == "exact-host-opencode-cli"
    argv, kwargs = calls[0]
    assert argv[:4] == [
        "docker",
        "exec",
        "mm-host-opencode",
        "/opt/venv/bin/python",
    ]
    assert "list_opencode_cli_model_options" in argv[-1]
    assert kwargs == {"timeout_seconds": 45.0, "check": False}


@pytest.mark.asyncio
async def test_exact_host_runner_probe_uses_authoritative_environment_builder() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((argv, kwargs))
            return 0, "ready", ""

    result = await _run_exact_host_runner_command(
        backend=Backend(),
        container_name="mm-host-opencode",
        argv=["gh", "auth", "status", "--hostname", "github.com"],
    )

    assert result == (0, "ready", "")
    argv, kwargs = calls[0]
    assert argv[:5] == [
        "docker",
        "exec",
        "mm-host-opencode",
        "/opt/venv/bin/python",
        "-c",
    ]
    assert "from omnigent.host.connect import _build_runner_env" in argv[5]
    assert argv[6:] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert kwargs == {"timeout_seconds": 30.0, "check": False}


@pytest.mark.asyncio
async def test_exact_host_opencode_probe_composes_both_environment_builders() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((argv, kwargs))
            return 0, "ready", ""

    result = await _run_exact_host_opencode_command(
        backend=Backend(),
        container_name="mm-host-opencode",
        argv=["gh", "auth", "status", "--hostname", "github.com"],
    )

    assert result == (0, "ready", "")
    argv, kwargs = calls[0]
    assert argv[:5] == [
        "docker",
        "exec",
        "mm-host-opencode",
        "/opt/venv/bin/python",
        "-c",
    ]
    assert "from omnigent.host.connect import _build_runner_env" in argv[5]
    assert "opencode_app_server.filtered_server_env" in argv[5]
    assert "moonmind-context" in argv[5]
    assert "env=runner_env" in argv[5]
    assert argv[6:] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert kwargs == {"timeout_seconds": 30.0, "check": False}


def _upstream_helper_layout(
    root: Path, *, drift: str | None = None, layout: str = "0.13"
) -> None:
    """Write a minimal package mirroring an admitted Omnigent helper layout.

    ``drift`` renames one helper the way an upstream release could, so the
    probe wrapper's typed substrate failure can be exercised for real.
    """

    packages = ["omnigent", "omnigent/host"]
    if layout == "0.13":
        packages.extend(["omnigent/harnesses", "omnigent/harnesses/opencode_native"])
    for package in packages:
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("", encoding="utf-8")
    runner_builder = (
        "_build_runner_environment" if drift == "runner" else "_build_runner_env"
    )
    (root / "omnigent" / "host" / "connect.py").write_text(
        f"def {runner_builder}(base_env, *, server_url, runner_id, binding_token, "
        "workspace, parent_pid, **_):\n"
        "    env = dict(base_env)\n"
        "    env['OMNIGENT_RUNNER_ID'] = runner_id\n"
        "    return env\n",
        encoding="utf-8",
    )
    filter_name = "filtered_serve_env" if drift == "opencode" else "filtered_server_env"
    app_server = root / (
        "omnigent/harnesses/opencode_native/app_server.py"
        if layout == "0.13"
        else "omnigent/opencode_native_app_server.py"
    )
    app_server.write_text(
        "import os\n"
        f"def {filter_name}(*, bridge_dir, auth_secret, extra_env=None):\n"
        "    return {key: value for key, value in os.environ.items() "
        "if key in ('PATH', 'HOME')}\n"
        "def list_opencode_cli_model_options():\n"
        f"    return [{{'id': 'replay/model', 'layout': {layout!r}}}]\n",
        encoding="utf-8",
    )


async def _capture_probe_source(run_probe) -> str:
    captured: dict[str, list[str]] = {}

    class Backend:
        async def run(self, argv, **kwargs):
            captured["argv"] = argv
            return 0, "", ""

    await run_probe(Backend())
    return captured["argv"][5]


@pytest.mark.asyncio
async def test_exact_host_runner_probe_source_runs_against_upstream_layout(
    tmp_path: Path,
) -> None:
    probe_source = await _capture_probe_source(
        lambda backend: _run_exact_host_runner_command(
            backend=backend, container_name="mm-host-opencode", argv=["true"]
        )
    )
    probed_source = (
        "import os, sys; sys.exit(3 if os.environ.get('OMNIGENT_RUNNER_ID') "
        "== 'moonmind-attestation' else 4)"
    )
    probed = [sys.executable, "-c", probed_source]
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}

    current = tmp_path / "current"
    _upstream_helper_layout(current)
    result = subprocess.run(
        [sys.executable, "-c", probe_source, *probed],
        cwd=current,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3, result.stderr
    assert "moonmind-attestation-substrate-unavailable" not in result.stderr

    drifted = tmp_path / "drifted"
    _upstream_helper_layout(drifted, drift="runner")
    result = subprocess.run(
        [sys.executable, "-c", probe_source, *probed],
        cwd=drifted,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 97
    assert "moonmind-attestation-substrate-unavailable: ImportError" in result.stderr
    assert "_build_runner_env" in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["0.12", "0.13"])
async def test_exact_host_opencode_probe_source_fails_typed_on_upstream_helper_drift(
    tmp_path: Path,
    layout: str,
) -> None:
    probe_source = await _capture_probe_source(
        lambda backend: _run_exact_host_opencode_command(
            backend=backend, container_name="mm-host-opencode", argv=["true"]
        )
    )
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}

    drifted = tmp_path / "drifted"
    _upstream_helper_layout(drifted, drift="opencode", layout=layout)
    result = subprocess.run(
        [sys.executable, "-c", probe_source, "true"],
        cwd=drifted,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 97
    assert "moonmind-attestation-substrate-unavailable: AttributeError" in result.stderr
    assert "filtered_server_env" in result.stderr

    # With the upstream helpers present, a missing MoonMind context shim is a
    # launch-contract failure on the probed path, never a substrate fault.
    current = tmp_path / "current"
    _upstream_helper_layout(current, layout=layout)
    result = subprocess.run(
        [sys.executable, "-c", probe_source, "true"],
        cwd=current,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode not in (0, 97)
    assert "moonmind-attestation-substrate-unavailable" not in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_probe", "boundary"),
    [
        (
            lambda backend: _run_exact_host_runner_command(
                backend=backend, container_name="mm-host-opencode", argv=["true"]
            ),
            "runner environment",
        ),
        (
            lambda backend: _run_exact_host_opencode_command(
                backend=backend, container_name="mm-host-opencode", argv=["true"]
            ),
            "OpenCode shell environment",
        ),
        (
            lambda backend: _read_exact_host_model_options(
                backend=backend,
                client=SimpleNamespace(),
                container_name="mm-host-opencode",
                omnigent_host_id="host-opencode",
                harness_id="opencode-native",
            ),
            "OpenCode model catalog",
        ),
    ],
    ids=["runner", "opencode-shell", "model-catalog"],
)
async def test_exact_host_probe_substrate_failure_is_typed_build_mismatch(
    run_probe, boundary: str
) -> None:
    class Backend:
        async def run(self, argv, **kwargs):
            return (
                97,
                "",
                "Traceback (most recent call last):\n  ...\n"
                "moonmind-attestation-substrate-unavailable: ModuleNotFoundError: "
                "No module named 'omnigent.harnesses'\n",
            )

    with pytest.raises(HarnessPlatformError) as excinfo:
        await run_probe(Backend())

    assert excinfo.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    assert str(excinfo.value) == (
        f"{boundary} attestation helper is unavailable in the exact host image: "
        "ModuleNotFoundError: No module named 'omnigent.harnesses'"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "stderr"),
    [(1, "test: failed"), (97, "probed command exited 97 without the marker")],
    ids=["ordinary-failure", "reserved-status-without-marker"],
)
async def test_exact_host_probe_contract_failures_keep_their_own_status(
    code: int, stderr: str
) -> None:
    class Backend:
        async def run(self, argv, **kwargs):
            return code, "", stderr

    assert await _run_exact_host_runner_command(
        backend=Backend(), container_name="mm-host-opencode", argv=["true"]
    ) == (code, "", stderr)
    assert await _run_exact_host_opencode_command(
        backend=Backend(), container_name="mm-host-opencode", argv=["true"]
    ) == (code, "", stderr)
    with pytest.raises(HarnessPlatformError) as excinfo:
        await _read_exact_host_model_options(
            backend=Backend(),
            client=SimpleNamespace(),
            container_name="mm-host-opencode",
            omnigent_host_id="host-opencode",
            harness_id="opencode-native",
        )
    assert excinfo.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_non_opencode_exact_host_model_options_use_tunnel() -> None:
    class Backend:
        async def run(self, *_args, **_kwargs):
            raise AssertionError("non-OpenCode harnesses must use the host tunnel")

    class Client:
        async def get_host_model_options(self, host_id, harness_id):
            assert (host_id, harness_id) == ("host-pi", "pi-native")
            return {"models": [{"id": "anthropic/claude-sonnet-4-6"}]}

    result, source = await _read_exact_host_model_options(
        backend=Backend(),
        client=Client(),
        container_name="mm-host-pi",
        omnigent_host_id="host-pi",
        harness_id="pi-native",
    )

    assert result == {"models": [{"id": "anthropic/claude-sonnet-4-6"}]}
    assert source == "omnigent-host-tunnel"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("return_value", "expected_message"),
    [
        (
            (1, "", "provider diagnostic containing sensitive context"),
            "exact host OpenCode model catalog probe failed",
        ),
        (
            (0, "not-json", ""),
            "exact host OpenCode model catalog probe returned invalid JSON",
        ),
        (
            (0, '{"models": {}}', ""),
            "exact host OpenCode model catalog probe returned an invalid catalog",
        ),
    ],
)
async def test_opencode_exact_host_model_options_fail_closed_without_diagnostics(
    return_value: tuple[int, str, str], expected_message: str
) -> None:
    class Backend:
        async def run(self, *_args, **_kwargs):
            return return_value

    with pytest.raises(HarnessPlatformError) as exc:
        await _read_exact_host_model_options(
            backend=Backend(),
            client=object(),
            container_name="mm-host-opencode",
            omnigent_host_id="host-opencode",
            harness_id="opencode-native",
        )

    assert str(exc.value) == expected_message
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE
    assert "sensitive context" not in str(exc.value)


@pytest.mark.parametrize("evidence", [None, {}, {"validatedAt": "2000-01-01", "imageRef": "old"}])
def test_profile_model_intent_does_not_depend_on_discovery(evidence):
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    provider = SimpleNamespace(
        runtime_id="opencode", provider_id="opencode", default_model="opencode/selected",
        default_effort="high", model_tiers=None, default_model_tier=1,
        model_catalog_evidence_json=evidence,
    )
    request = _request()
    profile = SimpleNamespace(model={"qualifiedId": "opencode-go/different", "effort": "low"})
    model, effort, route = service._resolve_model(request, profile, provider)
    assert (model, effort, route) == ("opencode/selected", "high", "opencode")


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code", "omnigent"])
def test_generic_opencode_slot_rejects_unrelated_provider_runtimes(runtime):
    profile = SimpleNamespace(
        harness=SimpleNamespace(id="opencode-native"), credentialSlots=[]
    )
    provider = SimpleNamespace(enabled=True, auth_state="connected", runtime_id=runtime)
    with pytest.raises(HarnessPlatformError):
        OmnigentExecutionPlanningService._verify_provider_profile(profile, provider)


@pytest.mark.parametrize("provider_id", ["openrouter", "vendor.v2_test"])
def test_generic_opencode_planning_preserves_selected_nested_route(provider_id):
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    profile = SimpleNamespace(
        harness=SimpleNamespace(id="opencode-native"), credentialSlots=[]
    )
    provider = SimpleNamespace(
        enabled=True,
        auth_state="connected",
        runtime_id="opencode",
        provider_id=provider_id,
        default_model=f"{provider_id}/author/model:free",
        default_effort=None,
        model_tiers=None,
        default_model_tier=1,
        model_catalog_evidence_json=None,
    )
    service._verify_provider_profile(profile, provider)
    assert service._resolve_model(_request(), profile, provider) == (
        f"{provider_id}/author/model:free",
        None,
        provider_id,
    )
    request = _request().model_copy(update={"parameters": {"model": "other/model"}})
    with pytest.raises(HarnessPlatformError):
        service._resolve_model(request, profile, provider)


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, key):
        return self._rows.get(key)


def _session_factory(rows):
    return lambda: _Session(rows)


class _InventoryClient:
    async def get_version(self) -> str:
        return "0.11.0"

    async def list_harnesses(self) -> list[dict[str, object]]:
        return [
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "capabilities": {
                    "integration_mode": "native-server",
                    "auth": "own-auth",
                    "model_family": "multi",
                    "effort": "none",
                    "interrupt": True,
                },
            },
            {
                "id": "third-party",
                "label": "Third Party",
                "package": "example.plugin",
                "plugin_entry_point": "example.plugin:harness",
                "plugin_load_error": "dependency unavailable",
            },
        ]

    async def list_agents(self) -> list[dict[str, object]]:
        return [{"id": "opencode-native-ui", "version": "7"}]

    async def list_hosts(self) -> list[dict[str, object]]:
        return [{"host_id": "host-1", "name": "connected", "status": "online"}]


@pytest.mark.asyncio
async def test_catalog_sync_binds_rows_to_real_build_and_persists_trust() -> None:
    repository = InMemoryHarnessCatalogRepository()
    service = OmnigentHarnessCatalogService(
        client=_InventoryClient(),
        repository=repository,
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "1" * 64,
        clock=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    result = await service.synchronize()

    assert result.snapshot.omnigentVersion == "0.11.0"
    assert result.snapshot.omnigentBuildDigest == "sha256:" + "1" * 64
    assert result.snapshot.sourceDigest != "sha256:" + "a" * 64
    assert result.diagnostics["agentCount"] == 1
    assert result.diagnostics["hostCount"] == 1
    assert await repository.load(result.snapshot.catalogRef) == result
    trust = {item.harnessId: item for item in result.trust_records}
    assert trust["opencode-native"].trustState is TrustState.core_trusted
    assert trust["third-party"].trustState is TrustState.quarantined
    assert trust["opencode-native"].implementation.digest not in {
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
    }
    opencode = next(
        row for row in result.snapshot.harnesses if row.id == "opencode-native"
    )
    assert opencode.capabilities.integrationMode == "native-server"
    assert opencode.capabilities.authModel == "own-auth"
    assert opencode.capabilities.modelFamily == "multi"
    assert opencode.capabilities.effortFamily == "none"


def _plan(model: str):
    digest = compute_model_config_digest(
        qualifiedId=model,
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "3" * 64,
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": "omnigent-credential-bindings:primary@1#sha256:"
            + "5" * 64,
            "credentialBindings": {
                "primary-model": {
                    "providerProfileRef": "opencode-go-primary",
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": "omnigent-support-combination:sha256:" + "0" * 64,
        }
    )


def _exact_plan(model: str):
    payload = _plan(model).payload.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "hostImageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentHostBuildDigest": "sha256:" + "1" * 64,
            "hostArchitecture": "linux/amd64",
            "policySnapshotRef": "artifact:policy",
            "policySnapshotDigest": "sha256:" + "2" * 64,
            "effectiveLaunchSnapshotRef": "artifact:launch",
            "effectiveLaunchSnapshotDigest": "sha256:" + "3" * 64,
        }
    )
    return create_execution_plan_envelope(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_ref", ["omnigent-on-demand@1", "omnigent-on-demand@2"])
@pytest.mark.parametrize("discovery", ["missing", "newer"])
@pytest.mark.parametrize("defect", ["none", "missing-host-build", "changed-artifact"])
async def test_planned_host_resolver_uses_exact_launch_artifact(
    policy_ref: str,
    monkeypatch,
    discovery,
    defect,
) -> None:
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "3" * 64,
        }
    )
    harness = HarnessRecord.model_validate(
        {
            "id": "opencode-native",
            "label": "OpenCode",
            "implementation": implementation.model_dump(mode="json", by_alias=True),
            "capabilities": {
                "integrationMode": "native-server",
                "authModel": "own-auth",
            },
        }
    )
    exact_image = "ghcr.io/example/admitted@sha256:" + "a" * 64
    launch = {
        "schemaVersion": 3,
        "launchPolicyRef": policy_ref,
        "harness": "opencode-native",
        "hostImageRef": exact_image,
        "hostMode": "on_demand_docker",
        "architectures": ["amd64"],
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "readOnlyRoot": True,
        "enforcedEgress": True,
        "egressProfileRef": "moonmind-omnigent-egress@1",
        "limits": {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "timeoutSeconds": 5400,
            "temporaryStorageMiB": 256,
        },
        "capture": {"required": True},
        "cleanup": {"mode": "remove", "janitor": True},
        "controlCapabilities": ["interrupt", "terminate"],
    }
    canonical = json.dumps(launch, sort_keys=True, separators=(",", ":"))
    launch["snapshotRef"] = (
        "omnigent-launch:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    )
    raw = json.dumps(launch, sort_keys=True, separators=(",", ":")).encode()
    payload = _plan("opencode-go/model").payload.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "harnessImplementationRef": implementation.implementation_ref(),
            "launchPolicyRef": policy_ref,
            "hostImageRef": exact_image,
            "omnigentHostBuildDigest": "sha256:" + "b" * 64,
            "hostArchitecture": "linux/amd64",
            "policySnapshotDigest": "sha256:" + "d" * 64,
            "effectiveLaunchSnapshotRef": "artifact:launch-1",
            "effectiveLaunchSnapshotDigest": (
                "sha256:" + hashlib.sha256(raw).hexdigest()
            ),
        }
    )
    plan = create_execution_plan_envelope(payload)
    plan = type(plan).model_validate_json(plan.model_dump_json(by_alias=True))
    if defect == "missing-host-build":
        plan = plan.model_copy(
            update={
                "payload": plan.payload.model_copy(
                    update={"omnigentHostBuildDigest": None}
                )
            }
        )

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.host_classes import (
        OmnigentHostClassSelector,
    )

    newer_image = "new-host@sha256:" + "e" * 64
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: (
            None
            if discovery == "missing"
            else SimpleNamespace(
                opencode_host_image_ref=newer_image,
                details={
                    "hostImageProvenance": {
                        newer_image: {
                            "version": "2.0.0",
                            "buildDigest": "sha256:" + "f" * 64,
                        }
                    }
                },
            )
        ),
    )

    class Catalogs:
        async def load(self, ref: str):
            assert ref == plan.payload.harnessCatalogRef
            return SimpleNamespace(
                snapshot=SimpleNamespace(
                    harnesses=(harness,),
                    omnigentVersion="1.0.0",
                    omnigentBuildDigest="sha256:" + "b" * 64,
                )
            )

    class Artifacts:
        async def read_bytes(self, ref: str) -> bytes:
            assert ref == "artifact:launch-1"
            return raw if defect != "changed-artifact" else raw + b"changed"

    resolver = OmnigentPlannedHostResolver(
        catalog_repository=Catalogs(),
        host_class_selector=OmnigentHostClassSelector(environment={}),
        artifact_gateway=Artifacts(),
    )
    if defect != "none":
        with pytest.raises(HarnessPlatformError) as failure:
            await resolver(plan)
        assert (
            failure.value.code
            == HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT
        )
        return
    host, policy = await resolver(plan)
    assert host.omnigentBuildDigest == plan.payload.omnigentHostBuildDigest
    assert host.omnigentVersion == "1.0.0"

    assert host.imageRef == exact_image
    assert host.runtime["uid"] == 1000
    assert policy.ref == policy_ref
    assert policy.limits["timeoutSeconds"] == 5400


@pytest.mark.asyncio
async def test_plan_usage_retry_keeps_first_plan_and_rejects_changed_request() -> None:
    plans = InMemoryExecutionPlanStore()
    usages = InMemoryExecutionPlanUsageStore(plans)
    identity = ExecutionPlanUsageIdentity("workflow-1", "step-1", "idem-1")
    compilation_count = 0

    async def compile_first():
        nonlocal compilation_count
        compilation_count += 1
        return _plan("opencode-go/first")

    request = {"parameters": {"model": "opencode-go/first"}}
    first = await usages.load_or_bind(
        identity=identity,
        request_payload=request,
        compile_fn=compile_first,
    )
    retry = await usages.load_or_bind(
        identity=identity,
        request_payload=json.loads(json.dumps(request)),
        compile_fn=lambda: _plan("opencode-go/rotated"),
    )

    assert retry.planRef == first.planRef
    assert compilation_count == 1

    with pytest.raises(HarnessPlatformError) as exc:
        await usages.load_or_bind(
            identity=identity,
            request_payload={"parameters": {"model": "opencode-go/changed"}},
            compile_fn=lambda: _plan("opencode-go/changed"),
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT.value
    )


@pytest.mark.asyncio
async def test_bundle_source_reloads_artifact_receipt_and_import_projection() -> None:
    bundle = b"immutable omnigent agent bundle"
    bundle_digest = "sha256:" + hashlib.sha256(bundle).hexdigest()
    imported_snapshot = {"id": "imported-1", "version": "7", "ready": True}
    imported_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                imported_snapshot,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    receipt_payload = {
        "bundleArtifactRef": "artifact:bundle-1",
        "bundleDigest": bundle_digest,
        "endpointRef": "default",
        "importedAgentId": "imported-1",
        "importedAgentVersion": "7",
        "importedContentDigest": imported_digest,
    }
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "endpointRef": "default",
            "source": {
                "kind": "bundle",
                "bundleArtifactRef": receipt_payload["bundleArtifactRef"],
                "bundleDigest": receipt_payload["bundleDigest"],
                "importedAgentId": receipt_payload["importedAgentId"],
                "importedAgentVersion": receipt_payload["importedAgentVersion"],
                "importedContentDigest": receipt_payload["importedContentDigest"],
                "importReceiptRef": _ref("omnigent-agent-import", receipt_payload),
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": "omnigent-harness-catalog:sha256:" + "1" * 64,
                "implementationRef": "omnigent-harness-implementation:sha256:"
                + "2" * 64,
            },
        }
    )

    class Artifacts:
        async def read_bytes(self, ref):
            assert ref == "artifact:bundle-1"
            return bundle

    class Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(
                available=True,
                compatible=True,
                error=None,
                metadata_snapshot=imported_snapshot,
            )

    class Session:
        async def execute(self, _statement):
            return Result()

    service = object.__new__(OmnigentExecutionPlanningService)
    service._artifacts = Artifacts()
    from api_service.db.models import OmnigentUpstreamAgentProjection

    await service._verify_agent_source(
        Session(), profile, OmnigentUpstreamAgentProjection
    )

    changed = profile.model_copy(
        update={
            "source": profile.source.model_copy(
                update={"bundleDigest": "sha256:" + "f" * 64}
            )
        }
    )
    with pytest.raises(HarnessPlatformError) as exc:
        await service._verify_agent_source(
            Session(), changed, OmnigentUpstreamAgentProjection
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE


class _DockerBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.runtime_ref = ""
        self.generation = ""

    async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
        self.calls.append((list(argv), input_bytes))
        if argv[1:3] == ["volume", "ls"]:
            ownership_template = argv[argv.index("--format") + 1]
            attested = (
                self.runtime_ref in ownership_template
                and json.dumps(self.generation) in ownership_template
            )
            return 0, (b"owned\n" if attested else b"mismatch\n"), b""
        return 0, b"", b""


class _Artifacts:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def write_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return "artifact://omnigent/test/credential-attestation.json"


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "opencode-primary",
            "correlationId": "workflow-1",
            "idempotencyKey": "idem-1",
            "parameters": {},
        }
    )


@pytest.mark.asyncio
async def test_planning_persists_exact_policy_and_effective_launch_bytes() -> None:
    writes: list[dict[str, object]] = []

    class Artifacts:
        async def write_bytes(self, **kwargs):
            writes.append(dict(kwargs))
            return f"artifact:{kwargs['name']}"

    service = object.__new__(OmnigentExecutionPlanningService)
    service._artifacts = Artifacts()
    policy = {
        "policyId": "omnigent-on-demand",
        "policyVersion": 2,
        "policyRef": "omnigent-on-demand@2",
        "boundaries": {},
        "validation": {},
    }
    launch = {
        "schemaVersion": 3,
        "launchPolicyRef": "omnigent-on-demand@2",
        "harness": "opencode-native",
        "hostImageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
    }

    result = await service._persist_exact_launch_authority(
        request=_request(),
        policy_snapshot=policy,
        effective_launch=launch,
    )

    assert result == (
        "artifact:launch-policy-snapshot.json",
        "sha256:" + hashlib.sha256(_canonical_json_bytes(policy)).hexdigest(),
        "artifact:effective-launch-snapshot.json",
        "sha256:" + hashlib.sha256(_canonical_json_bytes(launch)).hexdigest(),
    )
    assert [write["payload"] for write in writes] == [
        _canonical_json_bytes(policy),
        _canonical_json_bytes(launch),
    ]
    assert [write["link_type"] for write in writes] == [
        "input.launch_policy_snapshot",
        "input.effective_launch_snapshot",
    ]


@pytest.mark.asyncio
async def test_github_credential_projection_transports_secret_only_on_stdin(
    monkeypatch,
) -> None:
    secret = "github-secret-that-must-not-be-inspectable"

    async def acquire(**kwargs):
        assert kwargs["role"] == "collaboration" and kwargs["operation"] == "read"
        assert kwargs["execution_owner"] == "lease-owner-1"
        return SimpleNamespace(
            credential=EphemeralCredential(secret.encode()),
            binding=SimpleNamespace(endpoint="https://github.com"),
        )

    class Backend:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, argv, **kwargs):
            self.calls.append((list(argv), dict(kwargs)))
            if argv[1:3] == ["volume", "inspect"]:
                owner_ref = "lease-owner-1"
                return 0, hashlib.sha256(owner_ref.encode()).hexdigest()[:32], ""
            if argv[1] == "run":
                return 0, argv[-1], ""
            return 0, "", ""

    request = _request().model_copy(
        update={
            "workspace_spec": {
                "repositoryTarget": {
                    "provider": "git",
                    "repository": {"name": "MoonLadderStudios/Tactics"},
                }
            }
        }
    )
    backend = Backend()
    service = OmnigentGithubCredentialService(backend)
    monkeypatch.setattr(service, "acquire_repository_use", acquire)
    monkeypatch.setattr(
        service,
        "admitted_repository_identity",
        AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
    )
    attachment = await service.materialize(
        request=request,
        resolved_tools={
            "tools": ["gh", "git"],
            "repositoryAccess": {
                "collaboration": {"snapshotRef": "admitted-test-snapshot"}
            },
        },
        owner_ref="lease-owner-1",
        projection_reservation={
            "ownerRef": "lease-owner-1",
            "revision": next(_projection_revisions),
            "reservationId": str(uuid4()),
        },
        projection_verifier=AsyncMock(),
        writer_image_ref="ghcr.io/example/opencode@sha256:" + "1" * 64,
        runtime_uid=1000,
        runtime_gid=1000,
    )

    assert github_repository_from_request(request) == "MoonLadderStudios/Tactics"
    assert attachment is not None
    assert attachment["accessMode"] == "read-only"
    assert attachment["targetPath"] == "/run/mm-credentials/github"
    inspectable = json.dumps(
        {
            "calls": [argv for argv, _kwargs in backend.calls],
            "attachment": attachment,
        },
        sort_keys=True,
    )
    assert secret not in inspectable
    stdin_payloads = [
        kwargs.get("input_bytes")
        for _argv, kwargs in backend.calls
        if kwargs.get("input_bytes")
    ]
    assert stdin_payloads == [secret.encode()]
    writer_argv = next(
        argv for argv, kwargs in backend.calls if kwargs.get("input_bytes")
    )
    assert writer_argv[0:7] == [
        "docker",
        "run",
        "--rm",
        "-i",
        "--user",
        "0:0",
        "--network",
    ]


_projection_revisions = itertools.count(1)


class _ProjectionBackend:
    """Docker double that records the projection writer and fails on request."""

    def __init__(self, *, existing: bool, fail_writer: bool = False) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.existing = existing
        self.fail_writer = fail_writer
        self.digest = hashlib.sha256(b"lease-owner-1").hexdigest()[:32]

    async def run(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if argv[1:3] == ["volume", "create"]:
            self.existing = True
        if argv[1:3] == ["volume", "rm"]:
            self.existing = False
        if argv[1:3] == ["volume", "inspect"]:
            if not self.existing:
                return 1, "", "Error: no such volume"
            return 0, self.digest, ""
        if kwargs.get("input_bytes") and self.fail_writer:
            raise HarnessPlatformError(
                "writer interrupted",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        if argv[1] == "run":
            return (
                0,
                (
                    "null"
                    if "action = 'inspect'"
                    in shlex.split(argv[argv.index("-ceu") + 1])[3]
                    else argv[-1]
                ),
                "",
            )
        return 0, "", ""


async def _materialize_projection(monkeypatch, backend, secret: str):
    async def acquire(**_kwargs):
        return SimpleNamespace(
            credential=EphemeralCredential(secret.encode()),
            binding=SimpleNamespace(endpoint="https://github.com"),
        )

    service = OmnigentGithubCredentialService(backend)
    monkeypatch.setattr(service, "acquire_repository_use", acquire)
    monkeypatch.setattr(
        service,
        "admitted_repository_identity",
        AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
    )
    return await service.materialize(
        request=_request(),
        resolved_tools={
            "tools": ["gh", "git"],
            "repositoryAccess": {
                "collaboration": {"snapshotRef": "admitted-test-snapshot"}
            },
        },
        owner_ref="lease-owner-1",
        projection_reservation={
            "ownerRef": "lease-owner-1",
            "revision": next(_projection_revisions),
            "reservationId": str(uuid4()),
        },
        projection_verifier=AsyncMock(),
        writer_image_ref="ghcr.io/example/opencode:latest",
        runtime_uid=1000,
        runtime_gid=1000,
    )


def _projection_writer(backend, config_dir: Path) -> list[str]:
    """Execute the production reservation and return its matching writer."""
    from moonmind.omnigent.host_services.github_credentials import (
        github_projection_script,
    )

    argv = next(argv for argv, kwargs in backend.calls if kwargs.get("input_bytes"))
    uid, gid, host, stamp = argv[-4:]
    if os.geteuid() != 0:
        uid, gid = os.getuid(), os.getgid()
    args = ["--", str(uid), str(gid), host, stamp]
    subprocess.run(
        [
            "sh",
            "-ceu",
            github_projection_script(str(config_dir), action="reserve"),
            *args,
        ],
        check=True,
        capture_output=True,
    )
    return ["sh", "-ceu", github_projection_script(str(config_dir)), *args]


def _real_gh_token(config_dir: Path) -> str:
    """Read the projected credential with the real gh, as the mounted host does."""
    import shutil

    gh = shutil.which("gh") or "/opt/moonmind-tools/bin/gh"
    gh_home = config_dir.parent / "gh-home"
    gh_home.mkdir(exist_ok=True)
    (gh_home / "hosts.yml").write_bytes((config_dir / "hosts.yml").read_bytes())
    (gh_home / "config.yml").write_text('version: "1"\n')
    ambient = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    }
    environment = {
        key: value for key, value in os.environ.items() if key not in ambient
    }
    environment.update(
        {"GH_CONFIG_DIR": str(gh_home), "GH_HOST": "github.com", "HOME": str(gh_home)}
    )
    result = subprocess.run(
        [gh, "auth", "token"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_github_projection_interrupted_refresh_keeps_complete_issuance(
    monkeypatch, tmp_path
) -> None:
    # MoonLadderStudios/MoonMind#4011: the mounted reader must only ever see a
    # complete hosts.yml for a live issuance, even when a refresh is killed.
    config = tmp_path / "volume"
    first = _ProjectionBackend(existing=False)
    await _materialize_projection(monkeypatch, first, "selected-token-B1")
    subprocess.run(
        _projection_writer(first, config), input=b"selected-token-B1", check=True
    )
    complete = (config / "hosts.yml").read_bytes()
    assert _real_gh_token(config) == "selected-token-B1"

    refresh = _ProjectionBackend(existing=True)
    await _materialize_projection(monkeypatch, refresh, "selected-token-B2")
    writer = subprocess.Popen(
        _projection_writer(refresh, config), stdin=subprocess.PIPE
    )
    assert writer.stdin is not None
    writer.stdin.write(b"selected-tok")
    writer.stdin.flush()
    # Kill the writer once it has consumed the partial token from stdin.
    for _ in range(200):
        started = [path for path in config.iterdir() if path.name != "hosts.yml"]
        if started or (config / "hosts.yml").read_bytes() != complete:
            break
        await asyncio.sleep(0.01)
    writer.kill()
    writer.wait()

    assert (config / "hosts.yml").read_bytes() == complete
    assert _real_gh_token(config) == "selected-token-B1"

    subprocess.run(
        _projection_writer(refresh, config), input=b"selected-token-B2", check=True
    )
    assert _real_gh_token(config) == "selected-token-B2"
    assert oct((config / "hosts.yml").stat().st_mode & 0o777) == "0o600"


@pytest.mark.asyncio
async def test_github_projection_overlapping_writers_with_same_container_pid(
    monkeypatch, tmp_path
):
    # Both Docker writers commonly have PID1; ordering comes from durable
    # reservations, independent of PID namespaces and transport completion.
    config = tmp_path / "volume"
    old_backend = _ProjectionBackend(existing=True)
    await _materialize_projection(monkeypatch, old_backend, "older-value")
    old = await asyncio.to_thread(
        subprocess.Popen,
        _projection_writer(old_backend, config),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        old.stdin.write(b"older-")
        old.stdin.flush()
        new_backend = _ProjectionBackend(existing=True)
        await _materialize_projection(monkeypatch, new_backend, "newer-value")
        await asyncio.to_thread(
            subprocess.run,
            _projection_writer(new_backend, config),
            input=b"newer-value",
            check=True,
            capture_output=True,
        )
        installed = (config / "hosts.yml").read_bytes()
        await asyncio.to_thread(old.communicate, b"value", timeout=5)
        assert old.returncode != 0
        assert (config / "hosts.yml").read_bytes() == installed
        assert _real_gh_token(config) == "newer-value"
        assert (config / "hosts.yml").stat().st_mode & 0o777 == 0o600
    finally:
        if old.poll() is None:
            old.kill()
        await asyncio.to_thread(old.wait)


@pytest.mark.asyncio
@pytest.mark.parametrize("creator", ["successful", "failed"])
@pytest.mark.parametrize("failure", [HarnessPlatformError, asyncio.CancelledError])
async def test_github_projection_overlapping_failure_preserves_success(
    monkeypatch, creator, failure
) -> None:
    class Backend(_ProjectionBackend):
        def __init__(self):
            super().__init__(existing=False)
            self.inspections = 0
            self.both_inspected = asyncio.Event()
            self.created = asyncio.Event()
            self.published = asyncio.Event()
            self.live_token = None

        async def run(self, argv, **kwargs):
            task = asyncio.current_task().get_name()
            if argv[1:3] == ["volume", "inspect"] and self.inspections < 2:
                self.calls.append((list(argv), dict(kwargs)))
                self.inspections += 1
                if self.inspections == 2:
                    self.both_inspected.set()
                await self.both_inspected.wait()
                return 1, "", "Error: no such volume"
            if argv[1:3] == ["volume", "create"]:
                if task != creator:
                    await self.created.wait()
                result = await super().run(argv, **kwargs)
                self.created.set()
                return result
            if kwargs.get("input_bytes"):
                self.calls.append((list(argv), dict(kwargs)))
                if task == "failed":
                    await self.published.wait()
                    if failure is asyncio.CancelledError:
                        raise asyncio.CancelledError()
                    raise HarnessPlatformError(
                        "writer interrupted",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
                self.live_token = kwargs["input_bytes"]
                self.published.set()
                return 0, argv[-1], ""
            if argv[1:3] == ["volume", "rm"]:
                self.live_token = None
            return await super().run(argv, **kwargs)

    backend = Backend()
    results = await asyncio.wait_for(
        asyncio.gather(
            asyncio.create_task(
                _materialize_projection(monkeypatch, backend, "successful-token"),
                name="successful",
            ),
            asyncio.create_task(
                _materialize_projection(monkeypatch, backend, "failed-token"),
                name="failed",
            ),
            return_exceptions=True,
        ),
        timeout=5,
    )
    assert isinstance(results[0], dict)
    assert isinstance(results[1], failure)
    assert backend.existing, "failed overlapping writer deleted the live volume"
    assert backend.live_token == b"successful-token"
    assert not any(argv[1:3] == ["volume", "rm"] for argv, _kwargs in backend.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inspection_error",
    ["permission denied", "context deadline exceeded", "no such host: docker.invalid"],
)
async def test_github_projection_inspection_error_does_not_imply_absence(
    monkeypatch, inspection_error
) -> None:
    class Backend(_ProjectionBackend):
        async def run(self, argv, **kwargs):
            if argv[1:3] == ["volume", "inspect"] and not self.calls:
                self.calls.append((list(argv), dict(kwargs)))
                return 1, "", inspection_error
            return await super().run(argv, **kwargs)

    backend = Backend(existing=True)
    with pytest.raises(HarnessPlatformError) as exc:
        await _materialize_projection(monkeypatch, backend, "selected-token")
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
    )
    assert len(backend.calls) == 1
    assert backend.existing


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [True, False])
async def test_github_projection_rejects_foreign_volume_owner(
    monkeypatch, existing
) -> None:
    backend = _ProjectionBackend(existing=existing)
    backend.digest = "another-lease-owner"
    with pytest.raises(HarnessPlatformError) as exc:
        await _materialize_projection(monkeypatch, backend, "selected-token")
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT
    assert not any(kwargs.get("input_bytes") for _argv, kwargs in backend.calls)
    assert not any(argv[1:3] == ["volume", "rm"] for argv, _kwargs in backend.calls)


@pytest.mark.asyncio
async def test_github_projection_failed_rewrite_keeps_live_same_owner_volume(
    monkeypatch,
) -> None:
    live = _ProjectionBackend(existing=True, fail_writer=True)
    with pytest.raises(HarnessPlatformError):
        await _materialize_projection(monkeypatch, live, "selected-token-B")
    assert not any(argv[1:3] == ["volume", "rm"] for argv, _kwargs in live.calls)

    fresh = _ProjectionBackend(existing=False, fail_writer=True)
    with pytest.raises(HarnessPlatformError):
        await _materialize_projection(monkeypatch, fresh, "selected-token-B")
    assert fresh.existing
    assert not any(argv[1:3] == ["volume", "rm"] for argv, _kwargs in fresh.calls)
    # A same-owner retry can finish the retained preparation.
    fresh.fail_writer = False
    current_attachment = await _materialize_projection(
        monkeypatch, fresh, "replacement-token"
    )
    assert sum(argv[1:3] == ["volume", "create"] for argv, _kwargs in fresh.calls) == 1
    # The persisted, fenced lifecycle authority reclaims failed preparations.
    service = OmnigentGithubCredentialService(fresh)
    await service.cleanup(current_attachment)
    assert fresh.calls[-1][0][1:3] == ["volume", "rm"]
    assert not fresh.existing


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability_name,capability_file",
    [
        ("MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN", "execution-fanout"),
        ("MOONMIND_CONTAINER_JOBS_BEARER_TOKEN", "container-jobs"),
    ],
)
@pytest.mark.parametrize(
    "expected_host_id",
    [
        None,
        expected_omnigent_host_id("host-lease:one", 1),
        "d4ad2354-2db9-5413-9d35-92eff5f026bb",
    ],
    ids=["legacy-spec", "canonical-spec", "persisted-dashed-spec"],
)
@pytest.mark.parametrize(
    ("host_api_token", "expected_secret_payloads"),
    [
        (
            "host-control-token",
            [b"host-control-token", b"scoped-fanout-token"],
        ),
        ("", [b"scoped-fanout-token"]),
    ],
)
async def test_host_volume_initializers_use_setup_authority(
    host_api_token: str,
    expected_secret_payloads: list[bytes],
    expected_host_id: str | None,
    capability_name: str,
    capability_file: str,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            return (0, "container-id" if argv[1] == "create" else "", "")

    script_inputs = {}

    class Scripts:
        def build_entrypoint(self, **kwargs):
            script_inputs.update(kwargs)
            return "exec true", {}

    backend = Backend()
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=Scripts(),
        server_url="http://omnigent:8000",
        host_api_token=host_api_token,
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
    owner_ref = "host-lease:one"
    await launcher.launch(
        spec=HostLaunchSpec.model_validate(
            {
                "executionPlanRef": "plan:one",
                "stepExecutionId": "step-1",
                "runtimeBindingId": "binding-1",
                "hostLeaseRef": owner_ref,
                "hostLeaseGeneration": 1,
                "expectedOmnigentHostId": expected_host_id,
                "hostClassRef": host_class.ref,
                "imageRef": host_class.imageRef,
                "serverEndpointRef": "default",
                "serverUrl": "http://omnigent:8000",
                "networkRef": "moonmind_default",
                "limits": {"cpuMillis": 2000},
                "runtime": {},
                "correlationName": "mm-host-test",
                "workspaceAttachment": {
                    "kind": "bind",
                    "sourceRef": "/tmp/workspace",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                "skillAttachment": {
                    "kind": "bind",
                    "sourceRef": "/tmp/skills",
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                },
                "controlAttachment": launcher.control_attachment(
                    owner_ref,
                    require_capability_mount=True,
                ),
                "stateAttachment": {
                    "kind": "volume",
                    "sourceRef": "mm-host-state-test",
                    "targetPath": "/home/app/.omnigent",
                    "accessMode": "read-write",
                },
                "labels": {},
            }
        ),
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
        runtime_environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_AGENT_RUN_ID": "agent-run-1",
            "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
            "MOONMIND_STEP_ID": "step-1",
            "MOONMIND_RUNTIME_ID": "opencode-native",
            "MOONMIND_REPOSITORY_CONNECTION_REF": ("repository-connection:git-default"),
            capability_name: "scoped-fanout-token",
        },
    )

    setup_runs = [argv for argv, _kwargs in calls if argv[:2] == ["docker", "run"]]
    assert len(setup_runs) == len(expected_secret_payloads) + 1
    for argv in setup_runs:
        assert argv[argv.index("--user") + 1] == "0:0"
    workload_create = next(
        argv for argv, _kwargs in calls if argv[:2] == ["docker", "create"]
    )
    assert workload_create[workload_create.index("--user") + 1] == "1000:1000"
    from uuid import NAMESPACE_URL, uuid5

    launched_id = expected_host_id or str(uuid5(NAMESPACE_URL, owner_ref))
    assert f"OMNIGENT_HOST_ID={launched_id}" in workload_create
    assert "OMNIGENT_HOST_NAME=mm-host-test" in workload_create
    assert "scoped-fanout-token" not in json.dumps([argv for argv, _kwargs in calls])
    assert [
        kwargs["input_bytes"] for _argv, kwargs in calls if kwargs.get("input_bytes")
    ] == expected_secret_payloads
    assert script_inputs["runtime_environment"] == {
        "MOONMIND_URL": "http://api:8000",
        "MOONMIND_AGENT_RUN_ID": "agent-run-1",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
        "MOONMIND_STEP_ID": "step-1",
        "MOONMIND_RUNTIME_ID": "opencode-native",
        "MOONMIND_REPOSITORY_CONNECTION_REF": ("repository-connection:git-default"),
        capability_name + "_FILE": (f"/run/moonmind-host-auth/{capability_file}"),
    }
    assert script_inputs["control_credential_available"] is bool(host_api_token)


@pytest.mark.asyncio
async def test_capability_renewal_replaces_control_files_from_running_host_image() -> (
    None
):
    calls: list[tuple[list[str], dict[str, object]]] = []
    host_image = "sha256:" + "a" * 64

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            if argv[:2] == ["docker", "inspect"]:
                return (0, host_image + "\n", "")
            return (0, "", "")

    launcher = DockerOmnigentHostLauncher(
        backend=Backend(),
        runtime_scripts=object(),
        server_url="http://omnigent:8000",
        host_api_token="host-control-token",
    )

    renewed = await launcher.renew_capability_files(
        container_name="mm-host-1",
        control_volume="mm-omnigent-control-1",
        runtime_environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "fresh-container-token",
            "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN": "fresh-fanout-token",
        },
    )

    assert set(renewed) == {"container-jobs", "execution-fanout"}
    inspect_argv = calls[0][0]
    assert inspect_argv[:2] == ["docker", "inspect"]
    assert inspect_argv[-1] == "mm-host-1"
    writers = [(argv, kwargs) for argv, kwargs in calls if argv[:2] == ["docker", "run"]]
    assert sorted(kwargs["input_bytes"] for _argv, kwargs in writers) == [
        b"fresh-container-token",
        b"fresh-fanout-token",
    ]
    # Renewal runs while the session is live, so every Docker command is
    # bounded well below the host-lease heartbeat TTL.
    assert all(
        0 < float(kwargs["timeout_seconds"]) <= 60 for _argv, kwargs in calls
    )
    for argv, _kwargs in writers:
        # The writer uses the image the running host was launched from, owns
        # no network, and swaps the file in one rename so the CLI never reads
        # a truncated token mid-write.
        assert host_image in argv
        assert argv[argv.index("--network") + 1] == "none"
        assert "type=volume,src=mm-omnigent-control-1,dst=/control" in argv
        assert "mv -f" in argv[-1]
    assert "fresh-container-token" not in json.dumps([argv for argv, _ in calls])
    assert b"host-control-token" not in [
        kwargs.get("input_bytes") for _argv, kwargs in calls
    ]


@pytest.mark.asyncio
async def test_capability_renewal_without_bearers_touches_nothing() -> None:
    backend = SimpleNamespace(run=AsyncMock(return_value=(0, "", "")))
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=object(),
        server_url="http://omnigent:8000",
    )

    assert (
        await launcher.renew_capability_files(
            container_name="mm-host-1",
            control_volume="mm-omnigent-control-1",
            runtime_environment={"MOONMIND_URL": "http://api:8000"},
        )
        == ()
    )
    backend.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_runtime_renews_capability_from_persisted_host_context() -> None:
    """A long session gets a freshly minted capability for the same scope."""

    from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
    from moonmind.security.container_job_capabilities import (
        verify_container_job_session_capability,
    )

    renewals: list[dict[str, object]] = []

    class Launcher:
        server_url = "http://omnigent:8000"

        async def renew_capability_files(self, **kwargs):
            renewals.append(kwargs)
            return ("container-jobs",)

    runtime = GenericOmnigentHostRuntime(
        launcher=Launcher(),
        workspace_service=object(),
        skill_service=object(),
        tool_service=object(),
        github_credential_service=object(),
        egress_service=object(),
        runtime_environment_service=OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000",
            signing_secret="test-secret",
        ),
        registration_waiter=object(),
        host_attestor=object(),
        cleanup_service=object(),
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "step-1",
            "parameters": {"requiredCapabilities": ["docker"]},
            "workspaceSpec": {
                "workspaceLocator": {"kind": "sandbox", "workspaceId": "sandbox-1"}
            },
            "stepExecution": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "node-1",
                "executionOrdinal": 1,
                "stepExecutionId": "workflow-1:run-1:node-1:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    before = int(time.time())

    renewed = await runtime.renew_runtime_capabilities(
        request=request,
        plan=_plan("test/model"),
        host_lease_ref="lease-1",
        host_context={
            "containerName": "mm-host-1",
            "controlVolumeRef": "mm-omnigent-control-1",
            "capabilityRenewal": {
                "lifetimeSeconds": 5400,
                "workspaceAccessMode": "read-only",
            },
        },
    )

    assert renewed == ("container-jobs",)
    (renewal,) = renewals
    assert renewal["container_name"] == "mm-host-1"
    assert renewal["control_volume"] == "mm-omnigent-control-1"
    capability = verify_container_job_session_capability(
        renewal["runtime_environment"]["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"],
        secret="test-secret",
    )
    assert capability.session_id == "lease-1"
    assert capability.workspace_id == "sandbox-1"
    assert capability.workspace_read_only is True
    assert capability.expires_at >= before + 5400


@pytest.mark.parametrize(
    "harness_id", ["opencode-native", "codex-native", "claude-code-native"]
)
@pytest.mark.parametrize("relative_path", [None, "repo"])
def test_generic_container_capability_reaches_python_test_submission(
    harness_id, relative_path
):
    from fastapi import HTTPException

    from api_service.api.routers.mcp_tools import (
        ToolCallRequest,
        _enforce_container_capability_scope,
    )
    from moonmind.container_job_cli import python_test_submission
    from moonmind.security.container_job_capabilities import (
        verify_container_job_session_capability,
    )

    replay = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "integration/reliability/replays/issue-brief-verification-handoff/manifest.json"
        ).read_text()
    )["containerSubmission"]
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "step-1",
            "parameters": {"requiredCapabilities": replay["requiredCapabilities"]},
            "workspaceSpec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "sandbox-1",
                    **({"relativePath": relative_path} if relative_path else {}),
                }
            },
            "stepExecution": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "node-1",
                "executionOrdinal": 1,
                "stepExecutionId": "workflow-1:run-1:node-1:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    plan = _plan("test/model")
    plan = create_execution_plan_envelope(
        {
            **plan.payload.model_dump(mode="json", by_alias=True),
            "harnessId": harness_id,
        }
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000",
        signing_secret="test-secret",
    ).build(
        request=request,
        plan=plan,
        host_lease_ref="lease-1",
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        workspace_attachment={"accessMode": "read-only"},
    )
    capability = verify_container_job_session_capability(
        environment["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"],
        secret="test-secret",
    )
    submission = python_test_submission(replay["testTargets"], env=environment)
    assert submission["spec"]["workspaceRef"] == {
        "kind": "sandbox",
        "workspaceId": capability.workspace_id,
        "relativePath": "repo",
    }
    assert capability.workspace_id == "sandbox-1"
    assert capability.runtime_id == harness_id
    assert capability.workspace_read_only is True
    assert (
        capability.agent_run_id
        == capability.owner.principal_id
        == "workflow-1:run-1:node-1:execution:1"
    )
    assert submission["source"]["workflowId"] == capability.workflow_id == "workflow-1"
    assert (
        submission["source"]["omnigentConversationId"]
        == capability.session_id
        == "lease-1"
    )
    assert (
        environment["MOONMIND_CONTAINER_JOBS_MCP_URL"]
        == "http://api:8000/mcp/container"
    )
    assert "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN" not in environment
    _enforce_container_capability_scope(
        ToolCallRequest(tool="container.submit", arguments=submission),
        capability,
    )
    submission["spec"]["workspaceRef"]["workspaceId"] = "another-workspace"
    with pytest.raises(HTTPException) as denied:
        _enforce_container_capability_scope(
            ToolCallRequest(tool="container.submit", arguments=submission),
            capability,
        )
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "locator",
    [
        None,
        {"kind": "external_state", "artifactRef": "art_1"},
        {"kind": "sandbox", "workspaceId": "x", "relativePath": "../other"},
    ],
)
def test_generic_container_capability_rejects_missing_or_unsupported_workspace(locator):
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "wf",
            "idempotencyKey": "step",
            "parameters": {"requiredCapabilities": ["docker"]},
            "workspaceSpec": {"workspaceLocator": locator},
        }
    )
    with pytest.raises(HarnessPlatformError, match="sandbox workspace locator"):
        OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000", signing_secret="test"
        ).build(
            request=request,
            plan=_plan("test/model"),
            host_lease_ref="lease",
            launch_policy=get_launch_policy("omnigent-on-demand@1"),
        )


def test_generic_host_without_container_requirement_gets_no_container_authority():
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "wf",
            "idempotencyKey": "step",
            "parameters": {"requiredCapabilities": ["git"]},
        }
    )
    assert (
        OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000", signing_secret="test"
        ).build(
            request=request,
            plan=_plan("test/model"),
            host_lease_ref="lease",
            launch_policy=get_launch_policy("omnigent-on-demand@1"),
        )
        == {}
    )


def test_generic_host_mints_scoped_fanout_from_step_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "idem-1",
            "parameters": {"requiredCapabilities": ["gh", "execution.fanout"]},
            "workspaceSpec": {
                "repository": "MoonLadderStudios/MoonMind",
                "repositoryTarget": {
                    "provider": "git",
                    "connectionRef": "repository-connection:git-default",
                    "repository": {"name": "MoonLadderStudios/MoonMind"},
                    "branch": {"name": "main"},
                },
            },
            "stepExecution": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "node-1",
                "executionOrdinal": 1,
                "stepExecutionId": "workflow-1:run-1:node-1:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
                "skillSourcePolicy": {
                    "executionFanout": {
                        "authorized": True,
                        "selectedSkill": "batch-dependabot-resolver",
                        "sourceKind": "built_in",
                    }
                },
            },
        }
    )

    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000",
        signing_secret="test_jwt_secret_key",
    ).build(
        request=request,
        plan=_plan("opencode/muse-spark-1.2-contributor-free"),
        host_lease_ref="host-lease-1",
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
    )
    capability = verify_execution_fanout_capability(
        environment["MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN"],
        secret="test_jwt_secret_key",
    )

    assert environment["MOONMIND_URL"] == "http://api:8000"
    assert environment["MOONMIND_REPOSITORY_CONNECTION_REF"] == (
        "repository-connection:git-default"
    )
    assert capability.parent_workflow_id == "workflow-1"
    assert capability.agent_run_id == "workflow-1:run-1:node-1:execution:1"
    assert capability.session_id == "host-lease-1"
    assert capability.runtime_id == "opencode-native"
    assert capability.source_kind == "omnigent"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["opencode-go", "openrouter", "vendor.v2_test"])
async def test_opencode_volume_materialization_transports_secret_only_on_stdin(
    provider_id,
) -> None:
    secret = "super-sensitive-open-code-key"
    backend = _DockerBackend()
    artifacts = _Artifacts()
    lease = CredentialLease(
        profile_id="opencode-primary",
        runtime_id="opencode",
        lease_id="lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-primary",
        capacity_scope_ref="provider-profile:opencode-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=lease,
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="opencode-primary",
        credential_generation=4,
        values={"opencode_api_key": secret},
    )
    materializer = DockerOpencodeAuthJsonMaterializer(backend)

    handle = await materializer.materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/opencode@sha256:" + "1" * 64,
            artifact_gateway=artifacts,
            provider_route_ref=provider_id,
        )
    )
    backend.runtime_ref = handle.credentialRuntimeRef
    backend.generation = "4"

    inspectable = json.dumps(
        {
            "calls": [call[0] for call in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "artifacts": artifacts.payloads,
        },
        sort_keys=True,
    )
    assert secret not in inspectable
    stdin_payloads = [payload for _argv, payload in backend.calls if payload]
    assert len(stdin_payloads) == 1
    assert json.loads(stdin_payloads[0]) == {
        provider_id: {"type": "api", "key": secret},
    }
    assert artifacts.payloads[0]["providerRouteRef"] == provider_id
    writer_argv = next(argv for argv, payload in backend.calls if payload)
    assert writer_argv[0:7] == [
        "docker",
        "run",
        "--rm",
        "-i",
        "--user",
        "0:0",
        "--network",
    ]
    assert secrets.values == {}
    assert handle.credentialGeneration == 4
    # Credentials mount read-only into a staging directory; the runtime script
    # copies auth.json into the writable OpenCode data home so OpenCode can
    # still create repos/ and cache/ beside it.
    assert handle.attachments[0].targetPath == "/run/mm-credentials/opencode"
    assert handle.attachments[0].accessMode == "read-only"

    cleanup = await materializer.cleanup(handle, 4)
    assert cleanup.removed is True
    assert backend.calls[-1][0][1:3] == ["volume", "rm"]


@pytest.mark.asyncio
async def test_opencode_cleanup_replay_survives_command_output_redaction() -> None:
    replay_root = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "reliability"
        / "replays"
        / "omnigent-credential-cleanup-redaction"
    )
    manifest = json.loads((replay_root / "manifest.json").read_text())
    expected = json.loads((replay_root / "expected-outcome.json").read_text())

    class RedactingReplayBackend:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            del input_bytes, timeout_seconds
            command = list(argv)
            self.calls.append(command)
            if command[1:3] != ["volume", "ls"]:
                return 0, b"", b""
            ownership_template = command[command.index("--format") + 1]
            if (
                "{{if and" in ownership_template
                and manifest["handle"]["credentialRuntimeRef"]
                in ownership_template
                and json.dumps(str(manifest["expectedGeneration"]))
                in ownership_template
            ):
                return 0, expected["ownershipAttestation"].encode(), b""
            return 0, manifest["legacyRedactedInspectOutput"].encode(), b""

    backend = RedactingReplayBackend()
    handle = CredentialRuntimeHandle.model_validate(manifest["handle"])

    result = await DockerOpencodeAuthJsonMaterializer(backend).cleanup(
        handle,
        manifest["expectedGeneration"],
    )

    assert result.removed is expected["volumeRemoved"]
    assert expected["profileLeaseReleaseEligible"] is True
    assert backend.calls[-1][1:3] == ["volume", "rm"]
    inspect_template = backend.calls[0][backend.calls[0].index("--format") + 1]
    assert "{{if and" in inspect_template
    assert "owned{{else}}mismatch" in inspect_template


@pytest.mark.asyncio
async def test_opencode_cleanup_replay_treats_redaction_safe_absence_as_cleaned() -> (
    None
):
    replay_root = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "reliability"
        / "replays"
        / "omnigent-credential-cleanup-redaction"
    )
    manifest = json.loads((replay_root / "manifest.json").read_text())

    class MissingVolumeBackend:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            del input_bytes, timeout_seconds
            self.calls.append(list(argv))
            return 0, b"", b""

    backend = MissingVolumeBackend()
    handle = CredentialRuntimeHandle.model_validate(manifest["handle"])
    assert "no such volume" not in manifest["legacyRedactedMissingOutput"].lower()

    result = await DockerOpencodeAuthJsonMaterializer(backend).cleanup(
        handle,
        manifest["expectedGeneration"],
    )

    assert result.removed is True
    assert result.evidence == {"alreadyAbsent": True}
    assert len(backend.calls) == 1
    assert backend.calls[0][1:3] == ["volume", "ls"]


@pytest.mark.asyncio
async def test_opencode_cleanup_boolean_attestation_preserves_generation_fence() -> (
    None
):
    backend = _DockerBackend()
    backend.runtime_ref = "credential-runtime:sha256:" + "a" * 64
    backend.generation = "2"
    handle = CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": backend.runtime_ref,
            "providerProfileRef": "opencode-go-default",
            "providerLeaseRef": "provider-profile-lease:test",
            "credentialGeneration": 1,
            "materializerRef": "opencode-auth-json@1",
            "attachments": [
                {
                    "kind": "volume",
                    "sourceRef": "mm-omnigent-credential-test",
                    "targetPath": "/run/mm-credentials/opencode",
                    "accessMode": "read-only",
                }
            ],
            "cleanupRef": "credential-cleanup:sha256:" + "a" * 64,
        }
    )

    with pytest.raises(HarnessPlatformError) as exc:
        await DockerOpencodeAuthJsonMaterializer(backend).cleanup(handle, 1)

    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED
    )
    assert all(call[0][1:3] != ["volume", "rm"] for call in backend.calls)


@pytest.mark.asyncio
async def test_pi_provider_config_uses_same_generic_volume_contract() -> None:
    secret = "second-harness-provider-key"
    backend = _DockerBackend()
    artifacts = _Artifacts()
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="pi-anthropic-primary",
        capacity_scope_ref="provider-profile:pi-anthropic-primary",
        provider_lease_ref="provider-profile-lease:lease-pi",
        credential_generation=8,
        lease=CredentialLease(
            profile_id="pi-anthropic-primary",
            runtime_id="omnigent",
            lease_id="lease-pi",
            owner_id="owner-pi",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="pi-anthropic-primary",
        credential_generation=8,
        values={"api_key": secret},
    )

    handle = await DockerOmnigentProviderConfigMaterializer(backend).materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/pi@sha256:" + "2" * 64,
            artifact_gateway=artifacts,
            model_qualified_id="anthropic/claude-sonnet-4-6",
            provider_route_ref="anthropic",
        )
    )

    inspectable = json.dumps(
        {
            "calls": [call[0] for call in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "artifacts": artifacts.payloads,
        },
        sort_keys=True,
    )
    assert secret not in inspectable
    stdin_payloads = [payload for _argv, payload in backend.calls if payload]
    assert len(stdin_payloads) == 1
    config = json.loads(stdin_payloads[0])
    writer_argv = next(argv for argv, payload in backend.calls if payload)
    assert writer_argv[0:7] == [
        "docker",
        "run",
        "--rm",
        "-i",
        "--user",
        "0:0",
        "--network",
    ]
    assert config["providers"]["moonmind"]["default"] == ["anthropic", "pi"]
    assert config["providers"]["moonmind"]["anthropic"]["api_key"] == secret
    assert handle.materializerRef == "omnigent-provider-config@1"
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"
    }
    assert handle.attachments[0].accessMode == "read-only"
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_writer_ref_pulls_missing_digest_pinned_image() -> None:
    """A missing digest-pinned writer must be pulled, not failed (mm:feced5b2)."""

    class MissingThenPulledBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.image_present = False
            self.pulls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                if self.image_present:
                    return 0, b"sha256:abc\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                self.image_present = True
                return 0, b"Pulled\n", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingThenPulledBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    ref = "ghcr.io/example/opencode@sha256:" + "d" * 64
    resolved = await materializer._resolve_writer_ref(ref)
    assert resolved == ref
    assert backend.pulls == [["docker", "pull", ref]]


@pytest.mark.asyncio
async def test_writer_ref_still_fails_when_digest_pull_fails() -> None:
    class MissingPullFailsBackend(_DockerBackend):
        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                return 1, b"", b"pull access denied"
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingPullFailsBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    ref = "ghcr.io/example/opencode@sha256:" + "e" * 64
    with pytest.raises(HarnessPlatformError) as exc:
        await materializer._resolve_writer_ref(ref)
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
    )
    assert "pull the selected Host Class image" in str(exc.value)
    assert "pull access denied" in str(exc.value)


@pytest.mark.asyncio
async def test_writer_ref_converts_pull_timeout_to_materialization_failure() -> None:
    class PullTimeoutBackend(_DockerBackend):
        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                raise TimeoutError("timed out")
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = PullTimeoutBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    ref = "ghcr.io/example/opencode@sha256:" + "f" * 64
    with pytest.raises(HarnessPlatformError) as exc:
        await materializer._resolve_writer_ref(ref)
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
    )
    assert "timed out" in str(exc.value)


@pytest.mark.asyncio
async def test_writer_ref_reuses_qualified_local_instead_of_pulling(
    monkeypatch,
) -> None:
    """Patch/SHA drift reuses a qualified same-repo image without a 7GB pull."""

    requested = "ghcr.io/example/opencode@sha256:" + "a" * 64
    fallback = "ghcr.io/example/opencode@sha256:" + "b" * 64
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._candidate_deployed_refs",
        lambda: [fallback],
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._provenance_map",
        lambda: {
            fallback: {"version": "0.13.1", "buildDigest": "sha256:" + "d" * 64}
        },
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._operator_pins", lambda: []
    )

    class DriftBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pulls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                inspected = command[3] if len(command) > 3 else ""
                if inspected == fallback:
                    return 0, b"sha256:fallback\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                return 0, b"Pulled\n", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = DriftBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    resolved = await materializer._resolve_writer_ref(
        requested, expected_omnigent_version="0.13.0"
    )
    assert resolved == fallback
    assert backend.pulls == []


@pytest.mark.asyncio
async def test_writer_ref_rejects_unqualified_same_repo_fallback(
    monkeypatch,
) -> None:
    """Same-repo drift without observed provenance still pulls exact."""

    requested = "ghcr.io/example/opencode@sha256:" + "a" * 64
    fallback = "ghcr.io/example/opencode@sha256:" + "b" * 64
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._candidate_deployed_refs",
        lambda: [fallback],
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._provenance_map", lambda: {}
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_image_drift._operator_pins", lambda: []
    )

    class MissingBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pulls: list[list[str]] = []
            self.pulled = False

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                inspected = command[3] if len(command) > 3 else ""
                if inspected == fallback:
                    return 0, b"sha256:fallback\n", b""
                if inspected == requested and self.pulled:
                    return 0, b"sha256:requested\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                self.pulled = True
                return 0, b"Pulled\n", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    # Unqualified fallback is skipped; exact pull recovery still applies.
    resolved = await materializer._resolve_writer_ref(
        requested, expected_omnigent_version="0.13.0"
    )
    assert resolved == requested
    assert backend.pulls == [["docker", "pull", requested]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "github_failure,github_host",
    [
        (None, "github.com"),
        ("denied", "github.com"),
        ("diagnostics_unavailable", "github.com"),
        ("rate_limited", "github.com"),
        (None, "github.enterprise.test"),
    ],
)
async def test_sha_drift_replay_advances_digest_through_full_handoff(
    tmp_path, monkeypatch, github_failure, github_host
) -> None:
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", github_host)
    """Minimized replay of an escaped stale-digest dispatch failure.

    Advances the deployed digest in real resolved state, then runs the
    production handoff against the stale plan digest with no helper stubbed:
    deployed-fallback selection, full credential materialization, launch image
    resolution, exact-host attestation, and attested retry validation. Only
    Docker presence and the resolved-state path are faked.
    """

    repo = "ghcr.io/moonladderstudios/omnigent-host-moonmind"
    stale = repo + "@sha256:" + "a" * 64
    current = repo + "@sha256:" + "b" * 64
    stale_build = "sha256:" + "1" * 64
    current_build = "sha256:" + "2" * 64
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
                + "c" * 64,
                "sharedHostImageRef": current,
                "omnigentBuildDigest": "sha256:" + "c" * 64,
                "architecture": "linux/amd64",
                "details": {
                    "hostImageProvenance": {
                        current: {
                            "buildDigest": current_build,
                            "version": "0.13.1",
                        }
                    }
                },
            }
        )
    )

    class ReplayMaterializerBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pulls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                if command[3] == current:
                    return 0, b"sha256:present\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                return 1, b"", b"pull access denied"
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    # 1. Deployed-fallback selection through real resolved state.
    assert (
        compatible_deployed_fallback(
            stale, expected_omnigent_version="0.13.0"
        )
        == current
    )

    # 2. Full credential materialization against the stale plan digest.
    backend = ReplayMaterializerBackend()
    lease = CredentialLease(
        profile_id="opencode-primary",
        runtime_id="opencode",
        lease_id="lease-replay",
        owner_id="owner-replay",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-primary",
        capacity_scope_ref="provider-profile:opencode-primary",
        provider_lease_ref="provider-profile-lease:lease-replay",
        credential_generation=7,
        lease=lease,
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="opencode-primary",
        credential_generation=7,
        values={"opencode_api_key": "replay-key"},
    )
    handle = await DockerOpencodeAuthJsonMaterializer(backend).materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref=stale,
            artifact_gateway=_Artifacts(),
            provider_route_ref="opencode-go",
            expected_omnigent_version="0.13.0",
        )
    )
    assert handle.materializerRef == "opencode-auth-json@1"
    assert backend.pulls == []
    writer_argvs = [argv for argv, _ in backend.calls if argv[:2] == ["docker", "run"]]
    assert writer_argvs and all(current in argv for argv in writer_argvs)

    # 3. Launch image resolution reuses the same qualified digest.
    class ReplayLaunchBackend:
        async def run(
            self,
            argv,
            *,
            input_bytes=None,
            timeout_seconds=600.0,
            failure_code=None,
            check=True,
            output_limit_bytes=None,
        ):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                if command[3] == current:
                    return 0, "sha256:present", ""
                return 1, "", "No such image"
            raise AssertionError(command)

    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-pi",
            "version": 1,
            "imageRef": stale,
            "omnigentVersion": "0.13.0",
            "omnigentBuildDigest": stale_build,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "pi-native",
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [
                        {"name": "opencode", "version": "1.18.11"}
                    ],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["omnigent-provider-config@1", "none@1"],
            "features": {
                "git": True,
                "tmux": True,
                "bubblewrap": True,
                "workspaceBind": True,
                "readOnlyRoot": True,
                "restrictedEgress": True,
                "mountedSkills": True,
                "mountedTools": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )
    launcher = DockerOmnigentHostLauncher(
        backend=ReplayLaunchBackend(),
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url="http://omnigent:8000",
    )
    assert await launcher._resolve_launch_image(stale, host_class) == current

    # 4. Exact-host attestation on the fallback image (omnigent 0.13.1 and
    # opencode 1.18.12 prove patch drift within the admitted series).
    container = "mm-host-replay"
    profile = OMNIGENT_EGRESS_PROFILE
    applied_rule = "sha256:" + "9" * 64
    egress_labels = {
        "moonmind.owner": "generic-omnigent-host",
        "moonmind.egress.profile": profile.ref,
        "moonmind.egress.profile_digest": profile.digest,
        "moonmind.egress.applied_rule_digest": applied_rule,
    }
    egress_attestation = {
        "attestationRef": "artifact:egress-attestation",
        "profileRef": profile.ref,
        "profileDigest": profile.digest,
        "enforcerImplementation": "test",
        "backendRef": "test",
        "networkRef": profile.network_ref,
        "gatewayRef": profile.gateway_ref,
        "appliedRuleDigest": applied_rule,
        "configDigest": "sha256:" + "8" * 64,
        "gatewayImageDigest": "sha256:" + "7" * 64,
        "healthResult": "healthy",
        "validatedAt": "2026-09-15T00:00:00+00:00",
        "validationResult": "passed",
        "deniedConnectionCount": 0,
    }

    github_attempts = []
    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", AsyncMock()
    )

    class ReplayAttestBackend:
        async def inspect_container(self, container_name: str):
            assert container_name == container
            return {
                "Id": "cid-replay",
                "Config": {"Image": current, "Labels": dict(egress_labels)},
                "Mounts": [
                    {
                        "Name": "ws-vol",
                        "Destination": "/workspaces/run",
                        "RW": False,
                    },
                    {"Name": "skill-vol", "Destination": "/skills", "RW": False},
                    {
                        "Name": "github-vol",
                        "Destination": "/run/mm-credentials/github",
                        "RW": False,
                    },
                ],
            }

        async def run(
            self,
            argv,
            *,
            input_bytes=None,
            timeout_seconds=60.0,
            failure_code=None,
            check=True,
            output_limit_bytes=None,
        ):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                ref = command[-1]
                if "--format" in command:
                    assert ref in (current, "sha256:" + "f" * 64)
                    return 0, '"amd64"', ""
                assert ref == current
                return (
                    0,
                    json.dumps(
                        [
                            {
                                "RepoDigests": [current],
                                "Config": {
                                    "Labels": {
                                        "moonmind.omnigent.build_digest": (
                                            current_build
                                        )
                                    }
                                },
                                "Os": "linux",
                                "Architecture": "amd64",
                            }
                        ]
                    ),
                    "",
                )
            if command[1] == "inspect":
                return (
                    0,
                    json.dumps(
                        {
                            "labels": dict(egress_labels),
                            "networks": {
                                profile.network_ref: {
                                    "NetworkID": "net-1",
                                    "EndpointID": "ep-1",
                                    "IPAddress": "10.0.0.5",
                                }
                            },
                            "imageRef": current,
                            "image": "sha256:" + "f" * 64,
                        }
                    ),
                    "",
                )
            assert command[:2] == ["docker", "exec"]
            rest = command[3:]
            if "gh" in rest and "status" in rest:
                return 1, "", "account endpoint temporarily unavailable"
            if any("x-ratelimit" in str(arg).lower() or "--include" in str(arg) for arg in rest):
                return (
                    0,
                    "HTTP/2.0 403 Forbidden\nX-Ratelimit-Remaining: 0\n"
                    "X-Ratelimit-Reset: 1\nX-Ratelimit-Resource: graphql\n\n",
                    "",
                )
            if any("gh repo view" in str(arg) for arg in rest):
                github_attempts.append(command)
                if github_failure == "rate_limited":
                    return (
                        1,
                        "",
                        "GraphQL: API rate limit exceeded for user ID 16808547.",
                    )
                if github_failure:
                    return (
                        1,
                        "",
                        "HTTP 401: Bad credentials Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
                    )
                if len(github_attempts) == 1:
                    return 1, "", "dial tcp: temporary failure in name resolution"
                return (
                    0,
                    json.dumps(
                        {"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}
                    ),
                    "",
                )
            if "credential.helper" in rest:
                assert rest[-1] == f"https://{github_host}"
                return 0, "!/home/app/.omnigent/moonmind/bin/gh auth git-credential", ""
            if rest == ["/opt/venv/bin/omnigent", "--version"]:
                return 0, "omnigent 0.13.1 (built 2026-09-14T00:00:00Z)\n", ""
            if rest == ["opencode", "--version"]:
                return 0, "1.18.12\n", ""
            return 0, "", ""

    class ReplayArtifacts:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        async def write_json(self, **kwargs):
            if (
                github_failure == "diagnostics_unavailable"
                and kwargs["name"] == "github-access-preflight-failure.json"
            ):
                raise OSError("artifact store unavailable")
            self.payloads.append(kwargs["payload"])
            return f"artifact:{kwargs['name']}"

    class ReplayClient:
        async def get_host_model_options(self, host_id: str, harness_id: str):
            assert harness_id == "pi-native"
            return {"models": [{"qualifiedId": "pi/test-model"}]}

    attestor = DockerOmnigentHostAttestor(
        backend=ReplayAttestBackend(),
        client=ReplayClient(),
        artifacts=ReplayArtifacts(),
    )
    attest_plan = SimpleNamespace(
        payload=SimpleNamespace(
            harnessId="pi-native",
            harnessImplementationRef="omnigent-harness-implementation:sha256:"
            + "3" * 64,
            modelConfig=SimpleNamespace(
                qualifiedId="pi/test-model", routeRef="anthropic"
            ),
        )
    )
    spec = HostLaunchSpec.model_validate(
        {
            "executionPlanRef": "omnigent-execution-plan:sha256:" + "0" * 64,
            "stepExecutionId": "step-replay-1",
            "runtimeBindingId": "binding-replay",
            "hostLeaseRef": "lease-replay",
            "hostLeaseGeneration": 1,
            "hostClassRef": "omnigent-pi@1",
            "imageRef": stale,
            "serverEndpointRef": "default",
            "serverUrl": "http://omnigent:8000",
            "networkRef": profile.network_ref,
            "limits": {
                "cpuMillis": 2000,
                "memoryMiB": 4096,
                "processes": 256,
                "timeoutSeconds": 5400,
                "temporaryStorageMiB": 256,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
            "correlationName": container,
            "workspaceAttachment": {
                "kind": "volume",
                "sourceRef": "ws-vol",
                "targetPath": "/workspaces/run",
                "accessMode": "read-only",
            },
            "skillAttachment": {
                "kind": "volume",
                "sourceRef": "skill-vol",
                "targetPath": "/skills",
                "accessMode": "read-only",
                "deliveryRef": "skill-delivery:sha256:" + "1" * 64,
            },
            "toolAttachments": [],
            "githubCredentialAttachment": {
                "sourceRef": "github-vol",
                "targetPath": "/run/mm-credentials/github",
                "githubHost": github_host,
            },
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": "state-vol",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": dict(egress_labels),
        }
    )
    github_request = _request().model_copy(
        update={"workspace_spec": {"repository": "owner/repo"}}
    )
    attest_kwargs = dict(
        request=github_request,
        plan=attest_plan,
        spec=spec,
        host_class=host_class,
        launch_result={"containerName": container, "launchImageRef": current},
        registration={
            "omnigentHostId": "host-replay",
            "host": {"owner": "owner-replay"},
            "harnessReady": True,
        },
        credential_handles=[],
        egress_attestation=egress_attestation,
    )
    if github_failure == "rate_limited":
        # A temporarily unavailable GitHub API is not a host capability defect:
        # the typed code authorizes a fresh pre-session Step Execution, and the
        # original provider text survives for the operator.
        with pytest.raises(HarnessPlatformError, match="API rate limit") as raised:
            await attestor.attest(**attest_kwargs)
        assert (
            raised.value.code
            == HarnessPlatformFailure.OMNIGENT_EXTERNAL_SERVICE_UNAVAILABLE
        )
        assert remediation_for(raised.value.code) == "retry_step_execution"
        assert "diagnosticsRef:" in str(raised.value)
        return
    if github_failure:
        with pytest.raises(HarnessPlatformError, match="HTTP 401") as raised:
            await attestor.attest(**attest_kwargs)
        assert (
            raised.value.code
            == HarnessPlatformFailure.OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH
        )
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in str(raised.value)
        assert len(github_attempts) == 1
        if github_failure == "denied":
            assert "diagnosticsRef:" in str(raised.value)
            assert any(
                payload.get("phase") == "repository_access"
                for payload in attestor._artifacts.payloads
            )
        return
    attestations = await attestor.attest(**attest_kwargs)
    evidence = next(
        payload
        for payload in attestor._artifacts.payloads
        if isinstance(payload, dict)
        and payload.get("schemaVersion")
        == "moonmind.omnigent-exact-host-attestation.v1"
    )
    assert evidence["imageRef"] == current
    assert evidence["expectedImageRef"] == stale
    assert evidence["omnigentBuildDigest"] == current_build
    assert evidence["expectedOmnigentBuildDigest"] == stale_build
    assert evidence["githubCredentialMount"]["repositoryAuthorized"] is True
    assert len(github_attempts) == 3

    # 5. Attested retry validation consumes the drifted evidence.
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)

    class ReplayRetryArtifacts:
        async def read_bytes(self, _ref: str) -> bytes:
            return _canonical_json_bytes(
                {
                    "imageRef": evidence["imageRef"],
                    "architecture": "linux/amd64",
                    "omnigentBuildDigest": evidence["omnigentBuildDigest"],
                    "harnessId": "pi-native",
                    "harnessImplementationRef": (
                        "omnigent-harness-implementation:sha256:" + "3" * 64
                    ),
                    "omnigentHostId": evidence["omnigentHostId"],
                }
            )

    harness.realizer._artifacts = ReplayRetryArtifacts()
    await harness.realizer._validate_bound_host_identity(
        plan=SimpleNamespace(
            payload=SimpleNamespace(
                hostImageRef=stale,
                hostArchitecture="linux/amd64",
                omnigentHostBuildDigest=stale_build,
                harnessId="pi-native",
                harnessImplementationRef=(
                    "omnigent-harness-implementation:sha256:" + "3" * 64
                ),
            )
        ),
        host_context={
            "omnigentHostId": evidence["omnigentHostId"],
            "hostHarnessAttestationRef": attestations["hostHarnessAttestationRef"],
        },
    )


@pytest.mark.asyncio
async def test_opencode_materialize_recovers_missing_writer_via_pull() -> None:
    """Full materialize (production boundary) recovers a historic digest."""

    class MissingThenPulledBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.image_present = False
            self.pulls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                if self.image_present:
                    return 0, b"sha256:abc\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                self.image_present = True
                return 0, b"Pulled\n", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingThenPulledBackend()
    artifacts = _Artifacts()
    secret = "historic-digest-key"
    lease = CredentialLease(
        profile_id="opencode-primary",
        runtime_id="opencode",
        lease_id="lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-primary",
        capacity_scope_ref="provider-profile:opencode-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=lease,
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="opencode-primary",
        credential_generation=4,
        values={"opencode_api_key": secret},
    )
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    handle = await materializer.materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/opencode@sha256:" + "d" * 64,
            artifact_gateway=artifacts,
            provider_route_ref="opencode-go",
        )
    )
    assert handle.materializerRef == "opencode-auth-json@1"
    assert handle.attachments[0].accessMode == "read-only"
    assert handle.cleanupRef.startswith("credential-cleanup:sha256:")
    assert backend.pulls != []
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_writer_ref_does_not_pull_mutable_tag() -> None:
    class MissingMutableBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pulls = 0

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls += 1
                return 0, b"", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingMutableBackend()
    materializer = DockerOpencodeAuthJsonMaterializer(backend)
    ref = "ghcr.io/example/opencode:latest"
    resolved = await materializer._resolve_writer_ref(ref)
    assert resolved == ref
    assert backend.pulls == 0


@pytest.mark.asyncio
async def test_provider_config_materializer_recovers_missing_writer_via_pull() -> None:
    """provider-config must use the same pull recovery as opencode-auth-json."""

    class MissingThenPulledBackend(_DockerBackend):
        def __init__(self) -> None:
            super().__init__()
            self.image_present = False
            self.pulls: list[list[str]] = []

        async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
            command = list(argv)
            if command[1:3] == ["image", "inspect"]:
                if self.image_present:
                    return 0, b"sha256:abc\n", b""
                return 1, b"", b"No such image"
            if command[:2] == ["docker", "pull"]:
                self.pulls.append(command)
                self.image_present = True
                return 0, b"Pulled\n", b""
            return await super().run(
                argv, input_bytes=input_bytes, timeout_seconds=timeout_seconds
            )

    backend = MissingThenPulledBackend()
    artifacts = _Artifacts()
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="pi-anthropic-primary",
        capacity_scope_ref="provider-profile:pi-anthropic-primary",
        provider_lease_ref="provider-profile-lease:lease-pi",
        credential_generation=8,
        lease=CredentialLease(
            profile_id="pi-anthropic-primary",
            runtime_id="omnigent",
            lease_id="lease-pi",
            owner_id="owner-pi",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="pi-anthropic-primary",
        credential_generation=8,
        values={"api_key": "second-harness-provider-key"},
    )
    handle = await DockerOmnigentProviderConfigMaterializer(backend).materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/pi@sha256:" + "2" * 64,
            artifact_gateway=artifacts,
            model_qualified_id="anthropic/claude-sonnet-4-6",
            provider_route_ref="anthropic",
        )
    )
    assert handle.materializerRef == "omnigent-provider-config@1"
    assert backend.pulls == [
        ["docker", "pull", "ghcr.io/example/pi@sha256:" + "2" * 64]
    ]


@pytest.mark.asyncio
async def test_runtime_binding_identity_stays_stable_and_cas_fences_stale_updates() -> (
    None
):
    store = InMemoryStableRuntimeBindingStore()
    provider_leases = {
        "primary-model": {
            "providerProfileRef": "opencode-primary",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "credentialGeneration": 4,
            "credentialRuntimeRef": "pending",
            "materializerRef": "opencode-auth-json@1",
        }
    }
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="idem-1",
        provider_leases=provider_leases,
    )
    credentials = await store.update(
        initial.bindingId,
        expected_revision=1,
        expected_fencing_generation=1,
        state=RuntimeBindingState.credentials_materialized,
        updates={
            "credentialRuntimeHandles": {
                "primary-model": {
                    "credentialRuntimeRef": "credential-runtime:sha256:" + "2" * 64,
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "cleanupAuthorityRefs": ["credential-cleanup:sha256:" + "3" * 64],
        },
    )

    assert credentials.bindingId == initial.bindingId
    assert credentials.latestSnapshotRef != initial.latestSnapshotRef
    assert credentials.revision == 2
    assert credentials.providerLeases == initial.providerLeases

    with pytest.raises(HarnessPlatformError) as exc:
        await store.update(
            initial.bindingId,
            expected_revision=1,
            expected_fencing_generation=1,
            state=RuntimeBindingState.host_allocating,
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )


@pytest.mark.asyncio
async def test_runtime_binding_allows_usage_metrics_but_rejects_credential_keys() -> (
    None
):
    store = InMemoryStableRuntimeBindingStore()
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="idem-terminal-result",
        provider_leases={},
    )
    terminal = AgentRunResult(
        summary="complete",
        metrics={
            "tokenUsage": {
                "inputTokens": 12,
                "outputTokens": 4,
                "totalTokens": 16,
            }
        },
    )
    updated = await store.update(
        initial.bindingId,
        expected_revision=initial.revision,
        expected_fencing_generation=initial.fencingGeneration,
        updates={
            "terminalResult": terminal.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        },
    )
    assert updated.terminalResult == terminal.model_dump(
        by_alias=True, mode="json", exclude_none=True
    )

    with pytest.raises(ValueError, match="forbidden secret-bearing key key"):
        await store.update(
            updated.bindingId,
            expected_revision=updated.revision,
            expected_fencing_generation=updated.fencingGeneration,
            updates={
                "credentialRuntimeHandles": {"primary-model": {"key": "raw-credential"}}
            },
        )


@pytest.mark.asyncio
async def test_host_cleanup_claim_fences_a_stale_activity() -> None:
    repository = InMemoryOmnigentHostLeaseRepository()
    lease = await repository.acquire(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        runtime_binding_id="omnigent-runtime-binding:sha256:" + "b" * 64,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-on-demand@1",
        harness_id="opencode-native",
        harness_implementation_ref="omnigent-harness-implementation:sha256:" + "c" * 64,
        provider_profile_refs=("profile-a",),
    )
    ready = await repository.mark_ready(
        lease.leaseRef,
        expected_generation=lease.generation,
        omnigent_host_id="host-a",
        cleanup_handle={"containerName": "mm-host-a", "launchGeneration": 1},
    )
    claimed = await repository.claim_cleanup(
        ready.leaseRef, expected_generation=ready.generation
    )
    assert claimed.generation == ready.generation + 1
    assert claimed.launchGeneration == 1

    with pytest.raises(HarnessPlatformError) as exc:
        await repository.claim_cleanup(
            ready.leaseRef, expected_generation=ready.generation
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )


async def _generic_publication_harness(
    publication: dict[str, object],
    *,
    execution_state_notifier=None,
    capability_renewal: dict[str, object] | None = None,
    failing_renewals: int = 0,
    hanging_renewal: bool = False,
    session_seconds: float = 0.03,
) -> SimpleNamespace:
    """Build the real generic-host realizer around one publication outcome."""

    events: list[str] = []
    renewals: list[dict[str, object]] = []
    renewal_started: list[int] = []

    class CountingRuntimeBindings(InMemoryStableRuntimeBindingStore):
        def __init__(self) -> None:
            super().__init__()
            self.heartbeat_count = 0

        async def update(self, binding_id, **kwargs):
            if kwargs.get("state") is None and kwargs.get("updates") is None:
                self.heartbeat_count += 1
            return await super().update(binding_id, **kwargs)

    runtime_store = CountingRuntimeBindings()

    class CountingHostLeases(InMemoryOmnigentHostLeaseRepository):
        def __init__(self) -> None:
            super().__init__()
            self.heartbeat_count = 0
            self.heartbeat_ref = None

        async def heartbeat(self, lease_ref, **kwargs):
            self.heartbeat_count += 1
            self.heartbeat_ref = lease_ref
            return await super().heartbeat(lease_ref, **kwargs)

    host_leases = CountingHostLeases()
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-go-primary",
        capacity_scope_ref="provider-profile:opencode-go-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=CredentialLease(
            profile_id="opencode-go-primary",
            runtime_id="opencode",
            lease_id="lease-1",
            owner_id="owner-1",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )

    class Leases:
        async def acquire_all(self, **_kwargs):
            events.append("provider-acquired")
            return (acquired,)

        async def release_all(self, _leases):
            events.append("provider-released")

    handle = CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": "credential-runtime:sha256:" + "d" * 64,
            "providerProfileRef": "opencode-go-primary",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "credentialGeneration": 4,
            "materializerRef": "opencode-auth-json@1",
            "attachments": [
                {
                    "kind": "volume",
                    "sourceRef": "mm-credential-1",
                    "targetPath": "/home/app/.local/share/opencode",
                    "accessMode": "read-only",
                }
            ],
            "cleanupRef": "credential-cleanup:sha256:" + "e" * 64,
            "attestationRef": "artifact://credential-attestation",
        }
    )

    class Credentials:
        async def materialize_all(self, **_kwargs):
            events.append("credentials-materialized")
            return (handle,)

        async def load_cleanup_handles(self, *_args):
            events.append("credentials-reloaded")
            return (handle,)

        async def cleanup_all(self, handles):
            assert handles == (handle,)
            events.append("credentials-cleaned")
            return ()

    class HostRuntime:
        async def validate_repository_intent(self, *, request, plan):
            await OmnigentGithubCredentialService(None).validate_repository_intent(
                request=request, plan=plan
            )

        async def prepare(self, **kwargs):
            events.append("host-inputs-prepared")
            sink = kwargs["authority_sink"]
            await sink({"kind": "skills", "cleanupRef": "skill-cleanup:one"})
            from moonmind.omnigent.host_runtime import PreparedHostInputs

            return PreparedHostInputs(
                workspace_attachment={
                    "kind": "bind",
                    "sourceRef": "/tmp/work",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                skill_attachment={
                    "kind": "bind",
                    "sourceRef": "/tmp/skills",
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                    "deliveryRef": "skill-delivery:one",
                },
                tool_attachments=(),
                egress_attestation={
                    "networkRef": "egress",
                    "attestationRef": "artifact://egress",
                },
            )

        async def realize(self, **kwargs):
            events.append("host-ready")
            return {
                "omnigentHostId": "host-1",
                "hostId": "host-1",
                "containerName": "mm-host-1",
                "stateVolumeRef": "mm-state-1",
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": "/workspaces/run",
                "hostHarnessAttestationRef": "artifact://host",
                "modelOptionAttestationRef": "artifact://models",
                "hostCleanupRef": "host-cleanup:one",
                **(
                    {
                        "controlVolumeRef": "mm-control-1",
                        "capabilityRenewal": capability_renewal,
                    }
                    if capability_renewal is not None
                    else {}
                ),
            }

        async def renew_runtime_capabilities(self, **kwargs):
            renewals.append(kwargs)
            if hanging_renewal:
                renewal_started.append(host_leases.heartbeat_count)
                await asyncio.Event().wait()
            if len(renewals) <= failing_renewals:
                raise RuntimeError("docker daemon unavailable")
            return ("container-jobs",)

        async def cleanup(self, **_kwargs):
            events.append("host-cleaned")
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
            events.append("inputs-cleaned")

        async def cleanup_authorities(self, _authorities):
            events.append("inputs-cleaned")

    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )

    async def resolve_host(_plan):
        return host_class, get_launch_policy("omnigent-on-demand@1")

    async def session_driver(request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        assert session_authority_sink.binding.omnigentSessionId == "session-1"
        assert request.parameters["omnigent"]["session"]["hostId"] == "host-1"
        assert request.parameters["omnigent"]["capture"] == {
            "stream": False,
            "evidence": False,
        }
        authorization = request.parameters["omnigent"]["_moonmindProfileAuthorization"]
        assert (
            authorization["executionPlanRef"] == request.parameters["executionPlanRef"]
        )
        assert (
            authorization["runtimeBindingRef"]
            == request.parameters["runtimeBindingRef"]
        )
        assert authorization["providerProfileId"] == "opencode-go-primary"
        assert authorization["providerLeaseRef"] == ("provider-profile-lease:lease-1")
        assert authorization["credentialGeneration"] == 4
        assert authorization["hostBindingRef"]
        assert authorization["hostLeaseRef"]
        await asyncio.sleep(session_seconds)
        events.append("message-completed")
        return AgentRunResult(
            summary="done", metadata={"omnigentSessionId": "session-1"}
        )

    class SessionCleanup:
        async def drain(self, session_id):
            assert session_id == "session-1"
            events.append("session-drained")
            return {"sessionId": session_id, "stopped": True}

    class WorkspacePublisher:
        async def save_request_workspace(self, request):
            events.append("workspace-saved")
            return {"kind": "worktree_archive", "archiveRef": "artifact://saved", "archiveDigest": "sha256:" + "a" * 64}

        async def publish_request_workspace(self, **_kwargs):
            events.append("workspace-published")
            return dict(publication)

    class TurnCommands:
        async def claim(self, **kwargs):
            assert kwargs["payload_digest"] == _plan("opencode-go/model").planRef
            events.append("command-claimed")
            return SimpleNamespace(
                owns_delivery=True, session_id="oms_generic", fencing_generation=1
            )

        async def attach_provider_session(self, **kwargs):
            # The delivered provider session becomes canonical authority before
            # settlement, so provider-scoped lookups resolve this aggregate.
            events.append(
                f"provider-session-attached:{kwargs['provider_session_ref']}"
            )

        async def settle(self, **kwargs):
            events.append(f"command-settled:{kwargs['outcome'].value}")

    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=runtime_store,
        provider_lease_coordinator=Leases(),
        credential_provisioning_service=Credentials(),
        host_lease_repository=host_leases,
        host_runtime=HostRuntime(),
        planned_host_resolver=resolve_host,
        session_driver=session_driver,
        session_cleanup_service=SessionCleanup(),
        workspace_publisher=WorkspacePublisher(),
        turn_command_service=TurnCommands(),
        execution_state_notifier=execution_state_notifier,
        deployment_validator=AsyncMock(return_value=None),
        heartbeat_interval_seconds=0.005,
        heartbeat_ttl_seconds=60,
    )
    publish_request = _request().model_copy(
        update={
            "workspace_spec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": hashlib.sha256(
                        b"workflow-1:idem-1"
                    ).hexdigest()[:24],
                },
                "repository": "MoonLadderStudios/MoonMind",
                "startingBranch": "main",
            },
            "parameters": {
                "publishMode": "pr",
                "repository": "MoonLadderStudios/MoonMind",
            },
        }
    )
    return SimpleNamespace(
        realizer=realizer,
        publish_request=publish_request,
        events=events,
        renewals=renewals,
        renewal_started=renewal_started,
        runtime_store=runtime_store,
        host_leases=host_leases,
        acquired=acquired,
        credential_handle=handle,
    )


async def _prime_attested_host_binding(
    harness, plan, *, admission_epoch=0, host_context_extra=None
) -> None:
    """Leave one durable binding on an attested, ready host.

    This is the state a retry of an interrupted generic execution actually
    finds: provider leases and credentials already immutable, a ready host
    lease, and a published host attestation. ``_execute_lifecycle`` resumes it
    instead of launching a second host.
    """

    evidence = {
        "imageRef": plan.payload.hostImageRef,
        "architecture": plan.payload.hostArchitecture,
        "omnigentBuildDigest": plan.payload.omnigentHostBuildDigest,
        "harnessId": plan.payload.harnessId,
        "harnessImplementationRef": plan.payload.harnessImplementationRef,
        "omnigentHostId": "host-1",
    }

    class Artifacts:
        async def read_bytes(self, ref: str) -> bytes:
            assert ref == "artifact:host-attestation"
            return _canonical_json_bytes(evidence)

        async def write_json(self, **_kwargs):
            return "artifact:cleanup-attestation"

        async def write_text(self, **_kwargs):
            return "artifact:host-logs"

    harness.realizer._artifacts = Artifacts()
    materializer_ref = plan.payload.credentialBindings[
        "primary-model"
    ].materializerRef
    provider_authority = {
        "primary-model": {
            **harness.acquired.runtime_binding_value(
                credential_runtime_ref=credential_runtime_identity(
                    harness.acquired, materializer_ref
                )[0]
            ),
            "materializerRef": materializer_ref,
        }
    }
    binding = await harness.runtime_store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=harness.publish_request.idempotency_key,
        provider_leases=provider_authority,
        admission_epoch=admission_epoch,
    )
    binding = await harness.runtime_store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.credentials_materialized,
        updates={
            "credentialRuntimeHandles": {
                "primary-model": harness.credential_handle.model_dump(
                    by_alias=True, mode="json"
                )
            },
            "cleanupAuthorityRefs": [harness.credential_handle.cleanupRef],
        },
    )
    host_lease = await harness.host_leases.acquire(
        execution_plan_ref=plan.planRef,
        runtime_binding_id=binding.bindingId,
        host_class_ref=plan.payload.hostClassRef,
        launch_policy_ref=plan.payload.launchPolicyRef,
        harness_id=plan.payload.harnessId,
        harness_implementation_ref=plan.payload.harnessImplementationRef,
        provider_profile_refs=(harness.acquired.provider_profile_ref,),
    )
    binding = await harness.runtime_store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.host_allocating,
        updates={
            "hostBindingRef": host_lease.bindingRef,
            "hostLeaseRef": host_lease.leaseRef,
            "hostLeaseGeneration": host_lease.generation,
        },
    )
    host_lease = await harness.host_leases.mark_ready(
        host_lease.leaseRef,
        expected_generation=host_lease.generation,
        omnigent_host_id="host-1",
        cleanup_handle={
            "kind": "host",
            "containerName": "mm-host-1",
            "stateVolumeRef": "mm-state-1",
            "launchGeneration": host_lease.launchGeneration,
            **(host_context_extra or {}),
        },
    )
    await harness.runtime_store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.host_ready,
        updates={
            "omnigentHostId": "host-1",
            "hostLeaseGeneration": host_lease.generation,
            "attestationRefs": {
                "hostHarnessAttestationRef": "artifact:host-attestation"
            },
        },
    )


@pytest.mark.asyncio
async def test_long_session_renews_capabilities_before_they_expire() -> None:
    """A session outliving its launch timeout keeps a valid capability."""

    harness = await _generic_publication_harness(
        _PUSHED_PUBLICATION,
        capability_renewal={"lifetimeSeconds": 1, "workspaceAccessMode": "read-write"},
        failing_renewals=1,
        session_seconds=0.8,
    )

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.summary == "done"
    # The first renewal at half-life failed; the session kept running and the
    # next heartbeat renewed again instead of letting the token lapse.
    assert len(harness.renewals) >= 2
    renewal = harness.renewals[-1]
    assert renewal["host_lease_ref"] == harness.host_leases.heartbeat_ref
    assert renewal["host_context"]["controlVolumeRef"] == "mm-control-1"
    assert renewal["host_context"]["capabilityRenewal"] == {
        "lifetimeSeconds": 1,
        "workspaceAccessMode": "read-write",
    }
    assert renewal["request"].idempotency_key == harness.publish_request.idempotency_key


@pytest.mark.asyncio
async def test_short_session_does_not_renew_capabilities() -> None:
    harness = await _generic_publication_harness(
        _PUSHED_PUBLICATION,
        capability_renewal={
            "lifetimeSeconds": 5400,
            "workspaceAccessMode": "read-write",
        },
    )

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.summary == "done"
    assert harness.renewals == []


@pytest.mark.asyncio
async def test_resumed_host_renews_capabilities_before_session_starts() -> None:
    """A retried activity cannot know how much of the old token remains."""

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")
    await _prime_attested_host_binding(
        harness,
        plan,
        host_context_extra={
            "controlVolumeRef": "mm-control-1",
            "capabilityRenewal": {
                "lifetimeSeconds": 5400,
                "workspaceAccessMode": "read-write",
            },
        },
    )

    renewals_when_session_started: list[int] = []
    session_driver = harness.realizer._session_driver

    async def recording_session_driver(*args, **kwargs):
        renewals_when_session_started.append(len(harness.renewals))
        return await session_driver(*args, **kwargs)

    harness.realizer._session_driver = recording_session_driver

    result = await harness.realizer._execute_lifecycle(harness.publish_request, plan)

    assert result.summary == "done"
    assert len(harness.renewals) == 1
    assert harness.renewals[0]["host_context"]["controlVolumeRef"] == "mm-control-1"
    # The resumed agent may call `moonmind container` immediately, so the
    # renewal completes before the session is driven, not at a later heartbeat.
    assert renewals_when_session_started == [1]


@pytest.mark.asyncio
async def test_stalled_capability_renewal_does_not_block_lease_heartbeats() -> None:
    harness = await _generic_publication_harness(
        _PUSHED_PUBLICATION,
        capability_renewal={"lifetimeSeconds": 1, "workspaceAccessMode": "read-write"},
        hanging_renewal=True,
        session_seconds=0.8,
    )

    result = await asyncio.wait_for(
        harness.realizer.execute(harness.publish_request, _plan("opencode-go/model")),
        timeout=10,
    )

    assert result.summary == "done"
    (heartbeats_when_renewal_stalled,) = harness.renewal_started
    assert harness.host_leases.heartbeat_count > heartbeats_when_renewal_stalled + 5


@pytest.mark.asyncio
async def test_bound_host_retry_uses_attestation_not_current_deployment() -> None:
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")
    await _prime_attested_host_binding(harness, plan)
    harness.realizer._deployment_validator = lambda _payload: (_ for _ in ()).throw(
        AssertionError("continuation consulted mutable deployment identity")
    )
    harness.realizer._resolve_host = AsyncMock(
        side_effect=AssertionError("continuation re-resolved current host defaults")
    )

    result = await harness.realizer._execute_lifecycle(
        harness.publish_request,
        plan,
    )

    assert result.summary == "done"
    assert "credentials-reloaded" in harness.events
    harness.realizer._resolve_host.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_resume_reports_exactly_one_terminal_migration_outcome() -> None:
    """A resumed execution reports its own outcome once, and no launch.

    The attested-resume lifecycle owns the terminal cleanup outcome. When its
    session raises, the failure travels back out through the launch lifecycle,
    which must not add a second cleanup outcome or report a launch-readiness
    failure for a host it never launched.
    """

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")
    await _prime_attested_host_binding(harness, plan)

    async def _failing_session(_request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        raise RuntimeError("resumed provider session failed")

    harness.realizer._session_driver = _failing_session
    control_plane_metrics.reset()

    with pytest.raises(RuntimeError, match="resumed provider session failed"):
        await harness.realizer._execute_lifecycle(harness.publish_request, plan)

    cleanup = [
        (labels, count)
        for name, labels, count in control_plane_metrics.counter_series()
        if name == control_plane_metrics.MIGRATION_CLEANUP_OUTCOME
    ]
    assert len(cleanup) == 1 and cleanup[0][1] == 1, cleanup
    assert cleanup[0][0]["harness_class"] == "opencode"
    assert not [
        labels
        for name, labels, _count in control_plane_metrics.counter_series()
        if name == control_plane_metrics.MIGRATION_LAUNCH_READINESS
    ]


@pytest.mark.asyncio
async def test_bound_host_retry_rejects_mismatched_attestation() -> None:
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")

    class Artifacts:
        async def read_bytes(self, _ref: str) -> bytes:
            return _canonical_json_bytes(
                {
                    # Different repository is never compatible drift: patch
                    # and SHA may evolve within the same image family, but a
                    # foreign image family must still fail closed.
                    "imageRef": "ghcr.io/example/other@sha256:" + "9" * 64,
                    "architecture": plan.payload.hostArchitecture,
                    "omnigentBuildDigest": plan.payload.omnigentHostBuildDigest,
                    "harnessId": plan.payload.harnessId,
                    "harnessImplementationRef": (
                        plan.payload.harnessImplementationRef
                    ),
                    "omnigentHostId": "host-1",
                }
            )

    harness.realizer._artifacts = Artifacts()
    with pytest.raises(HarnessPlatformError, match="retry identity conflicts"):
        await harness.realizer._validate_bound_host_identity(
            plan=plan,
            host_context={
                "omnigentHostId": "host-1",
                "hostHarnessAttestationRef": "artifact:host-attestation",
            },
        )


@pytest.mark.asyncio
async def test_bound_host_retry_allows_same_repo_sha_drift() -> None:
    """Same-repository SHA/patch drift survives attested retries.

    Rebuilt host images change digests while keeping major.minor. Retries must
    not fail on SHA alone; major.minor compatibility is enforced downstream.
    """

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")

    class Artifacts:
        async def read_bytes(self, _ref: str) -> bytes:
            return _canonical_json_bytes(
                {
                    "imageRef": "ghcr.io/example/opencode@sha256:" + "9" * 64,
                    "architecture": plan.payload.hostArchitecture,
                    "omnigentBuildDigest": plan.payload.omnigentHostBuildDigest,
                    "harnessId": plan.payload.harnessId,
                    "harnessImplementationRef": (
                        plan.payload.harnessImplementationRef
                    ),
                    "omnigentHostId": "host-1",
                }
            )

    harness.realizer._artifacts = Artifacts()
    # Same repo, different SHA: must not raise.
    await harness.realizer._validate_bound_host_identity(
        plan=plan,
        host_context={
            "omnigentHostId": "host-1",
            "hostHarnessAttestationRef": "artifact:host-attestation",
        },
    )


@pytest.mark.asyncio
async def test_bound_host_retry_rejects_build_only_mismatch() -> None:
    """A lone build mismatch against the expected image is not drift."""

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _exact_plan("opencode-go/model")

    class Artifacts:
        async def read_bytes(self, _ref: str) -> bytes:
            return _canonical_json_bytes(
                {
                    "imageRef": plan.payload.hostImageRef,
                    "architecture": plan.payload.hostArchitecture,
                    "omnigentBuildDigest": "sha256:" + "9" * 64,
                    "harnessId": plan.payload.harnessId,
                    "harnessImplementationRef": (
                        plan.payload.harnessImplementationRef
                    ),
                    "omnigentHostId": "host-1",
                }
            )

    harness.realizer._artifacts = Artifacts()
    with pytest.raises(HarnessPlatformError, match="retry identity conflicts"):
        await harness.realizer._validate_bound_host_identity(
            plan=plan,
            host_context={
                "omnigentHostId": "host-1",
                "hostHarnessAttestationRef": "artifact:host-attestation",
            },
        )


@pytest.mark.asyncio
async def test_fresh_host_launch_checks_current_deployment_before_prepare() -> None:
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)

    class StaleDeployment(ValueError):
        pass

    harness.realizer._deployment_validator = lambda _payload: (_ for _ in ()).throw(
        StaleDeployment("stale host image")
    )

    with pytest.raises(StaleDeployment, match="stale host image"):
        await harness.realizer.execute(
            harness.publish_request,
            _plan("opencode-go/model"),
        )

    assert "host-inputs-prepared" not in harness.events
    assert "provider-acquired" not in harness.events
    assert "credentials-materialized" not in harness.events
    assert "command-claimed" not in harness.events


_PUSHED_PUBLICATION: dict[str, object] = {
    "push_status": "pushed",
    "push_branch": "moonmind-job-test",
    "push_base_branch": "main",
    "push_head_sha": "a" * 40,
    "push_commit_count": 1,
    "remote_verified": True,
}

# The publisher already proved the workspace head is exactly the remote base
# head, so a step that legitimately adds no commits lost no repository work.
_NO_COMMIT_PUBLICATION: dict[str, object] = {
    "push_status": "no_commits",
    "push_branch": "moonmind-job-test",
    "push_base_branch": "moonmind-job-test",
    "push_head_sha": "b" * 40,
    "push_commit_count": 0,
    "remote_verified": True,
}


@pytest.mark.asyncio
async def test_generic_realizer_persists_authority_and_releases_provider_last() -> None:
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer
    publish_request = harness.publish_request
    events = harness.events
    runtime_store = harness.runtime_store
    host_leases = harness.host_leases

    result = await realizer.execute(publish_request, _plan("opencode-go/model"))

    assert result.summary == "done"
    assert result.metadata["executionPlanRef"] == _plan("opencode-go/model").planRef
    assert result.metadata["runtimeBindingRef"].startswith(
        "omnigent-runtime-binding:sha256:"
    )
    assert (
        result.metadata["supportCombinationIdentity"]["supportCombinationKey"]
        == _plan("opencode-go/model").payload.supportCombinationKey
    )
    assert events[-1] == "command-settled:applied"
    assert events.index("command-claimed") < events.index("provider-acquired")
    assert events.index("provider-released") < events.index("command-settled:applied")
    # The delivered provider session is attached to canonical authority before
    # settlement, so a later provider-scoped lookup resolves this aggregate
    # instead of bootstrapping a second one.
    assert events.index(
        "provider-session-attached:session-1"
    ) < events.index("command-settled:applied")
    assert events.index("host-cleaned") < events.index("credentials-cleaned")
    assert events.index("workspace-published") < events.index("host-cleaned")
    assert events.index("credentials-cleaned") < events.index("provider-released")
    assert host_leases.heartbeat_count >= 1
    assert runtime_store.heartbeat_count >= 1

    assert result.metadata["push_status"] == "pushed"
    assert result.metadata["acceptedRepositoryEvidence"] == {
        "schemaVersion": "accepted-repository-evidence/v1",
        "pushStatus": "pushed",
        "branch": "moonmind-job-test",
        "baseBranch": "main",
        "headSha": "a" * 40,
        "commitsAheadOfBase": 1,
        "repositoryChanged": True,
        "publicationAuthorized": True,
        "candidateContaminated": False,
        "remoteVerified": True,
        "authority": "omnigent.generic_host_execution",
    }

    first_execution_events = tuple(events)
    replay = await realizer.execute(publish_request, _plan("opencode-go/model"))

    assert replay.summary == "done"
    assert events[len(first_execution_events) :] == []


@pytest.mark.asyncio
async def test_generic_host_retains_leases_through_active_tool_only_response() -> None:
    from moonmind.omnigent.execute import _await_marked_turn_terminal

    manifest_path = Path(__file__).resolve().parents[2] / "integration/reliability/replays/omnigent-running-tool-output-terminal/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    active = manifest["terminalSnapshot"]
    inactive = {**active, "status": "idle", "active_response_id": None}

    class Client:
        calls = 0

        async def get_session(self, _session_id):
            assert "host-cleaned" not in harness.events
            assert "provider-released" not in harness.events
            self.calls += 1
            return active if self.calls < 20 else inactive

    async def session_driver(request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        status, snapshot = await _await_marked_turn_terminal(
            client=Client(),
            session_id="session-1",
            marker=manifest["currentTurnMarker"],
            baseline_item_ids=frozenset(manifest["preDispatchItemIds"]),
            event_count=1,
            terminal_status="completed",
            interval_seconds=0.001,
            quiet_period_seconds=0.002,
            tool_only_quiet_period_seconds=0.002,
        )
        assert status == "completed" and snapshot is inactive
        harness.events.append("terminal-observed")
        return AgentRunResult(
            summary="done", metadata={"omnigentSessionId": "session-1"}
        )

    harness.realizer._session_driver = session_driver
    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )
    assert result.failure_class is None
    assert harness.events.index("terminal-observed") < harness.events.index(
        "host-cleaned"
    )
    replay = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )
    assert replay == result


def _metric_keys(section: str, name: str) -> list[str]:
    """Return the recorded aggregate keys for one metric family."""

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    return [
        key
        for key in control_plane_metrics.snapshot()[section]
        if key.startswith(f"{name}[")
    ]


@pytest.mark.asyncio
async def test_generic_realizer_emits_migration_telemetry_from_its_lifecycle() -> None:
    """Every migration family is emitted by the production lifecycle itself."""

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    control_plane_metrics.reset()

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.failure_class is None
    counters = control_plane_metrics.snapshot()["counters"]
    assert counters[
        f"{control_plane_metrics.MIGRATION_LAUNCH_READINESS}"
        "[('harness_class', 'opencode'), ('readiness', 'ready')]"
    ] == 1
    assert counters[
        f"{control_plane_metrics.MIGRATION_CLEANUP_OUTCOME}"
        "[('harness_class', 'opencode'), ('cleanup_outcome', 'completed_clean')]"
    ] == 1
    for family in (
        control_plane_metrics.MIGRATION_PROVIDER_PROFILE_WAIT,
        control_plane_metrics.MIGRATION_HOST_LATENCY,
        control_plane_metrics.MIGRATION_FIRST_TURN_LATENCY,
    ):
        observed = _metric_keys("observations", family)
        assert observed == [f"{family}[('harness_class', 'opencode')]"], family
    # No identity may reach the label space through any of these call sites.
    serialized = repr(control_plane_metrics.snapshot())
    for forbidden in ("workflow-1", "idem-1", "session-1", "host-1", "p1"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_generic_realizer_reports_a_launch_that_never_became_ready() -> None:
    """A host that never reaches attested ready is a launch-readiness failure."""

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)

    async def _unavailable_host(_plan_envelope):
        raise RuntimeError("host class is unavailable")

    harness.realizer._resolve_host = _unavailable_host
    control_plane_metrics.reset()

    with pytest.raises(RuntimeError):
        await harness.realizer.execute(
            harness.publish_request, _plan("opencode-go/model")
        )

    counters = control_plane_metrics.snapshot()["counters"]
    assert counters[
        f"{control_plane_metrics.MIGRATION_LAUNCH_READINESS}"
        "[('harness_class', 'opencode'), ('readiness', 'not_ready')]"
    ] == 1
    assert not _metric_keys(
        "observations", control_plane_metrics.MIGRATION_FIRST_TURN_LATENCY
    )
    # The provider capacity wait is observed even when the launch fails after it.
    assert _metric_keys(
        "observations", control_plane_metrics.MIGRATION_PROVIDER_PROFILE_WAIT
    )


@pytest.mark.asyncio
async def test_generic_realizer_projects_provider_capacity_wait() -> None:
    projected: list[tuple[str, str, str]] = []

    async def notify(workflow_id: str, state: str, reason: str) -> None:
        projected.append((workflow_id, state, reason))

    harness = await _generic_publication_harness(
        _PUSHED_PUBLICATION,
        execution_state_notifier=notify,
    )

    result = await harness.realizer.execute(
        harness.publish_request,
        _plan("opencode-go/model"),
    )

    assert result.failure_class is None
    assert projected == [
        (
            "workflow-1",
            "awaiting_slot",
            "Waiting for Provider Profile capacity.",
        ),
        (
            "workflow-1",
            "launching",
            "Preparing runtime: validating the execution host and selected model.",
        ),
        ("workflow-1", "running", "Agent is running."),
    ]


@pytest.mark.asyncio
async def test_generic_realizer_ignores_state_projection_failure() -> None:
    async def unavailable_notifier(
        _workflow_id: str,
        _state: str,
        _reason: str,
    ) -> None:
        raise RuntimeError("projection unavailable")

    harness = await _generic_publication_harness(
        _PUSHED_PUBLICATION,
        execution_state_notifier=unavailable_notifier,
    )

    result = await harness.realizer.execute(
        harness.publish_request,
        _plan("opencode-go/model"),
    )

    assert result.failure_class is None
    assert result.summary == "done"


@pytest.mark.asyncio
async def test_generic_realizer_publishes_host_log_tail_as_cleanup_artifact() -> None:
    """The host log tail leaves cleanup evidence as a linked artifact, never inline."""

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer

    class Artifacts:
        def __init__(self) -> None:
            self.text_writes: list[dict[str, object]] = []
            self.json_writes: list[dict[str, object]] = []

        async def write_text(self, **kwargs):
            self.text_writes.append(kwargs)
            return "artifact:host-logs-1"

        async def write_json(self, **kwargs):
            self.json_writes.append(kwargs)
            return "artifact:cleanup-1"

    artifacts = Artifacts()
    realizer._artifacts = artifacts

    async def cleanup_with_logs(**_kwargs):
        harness.events.append("host-cleaned")
        return {
            "containerRemoved": True,
            "hostLogs": "runner: Turn started\nopencode: provider rejected turn\n",
            "hostLogsTruncated": False,
        }

    realizer._host_runtime.cleanup = cleanup_with_logs

    result = await realizer.execute(harness.publish_request, _plan("opencode-go/model"))

    assert result.summary == "done"
    assert [write["name"] for write in artifacts.text_writes] == [
        "generic-host-logs.txt"
    ]
    assert artifacts.text_writes[0]["link_type"] == "evidence.host_logs"
    assert artifacts.text_writes[0]["payload"].startswith("runner: Turn started")
    cleanup_payload = next(
        write["payload"]
        for write in artifacts.json_writes
        if write["name"] == "generic-host-cleanup.json"
    )
    host_results = cleanup_payload["results"]["host"]
    assert host_results["hostLogsRef"] == "artifact:host-logs-1"
    assert (
        "hostLogs" not in host_results
    ), "raw log text must not enter cleanup evidence"
    assert host_results["containerRemoved"] is True


@pytest.mark.asyncio
async def test_generic_realizer_records_host_log_publication_failure() -> None:
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer

    class Artifacts:
        def __init__(self) -> None:
            self.json_writes: list[dict[str, object]] = []

        async def write_text(self, **kwargs):
            raise OSError("artifact store unavailable")

        async def write_json(self, **kwargs):
            self.json_writes.append(kwargs)
            return "artifact:cleanup-1"

    artifacts = Artifacts()
    realizer._artifacts = artifacts

    async def cleanup_with_logs(**_kwargs):
        harness.events.append("host-cleaned")
        return {"containerRemoved": True, "hostLogs": "some output\n"}

    realizer._host_runtime.cleanup = cleanup_with_logs

    result = await realizer.execute(harness.publish_request, _plan("opencode-go/model"))

    assert result.summary == "done"
    host_results = next(
        write["payload"]
        for write in artifacts.json_writes
        if write["name"] == "generic-host-cleanup.json"
    )["results"]["host"]
    assert host_results["hostLogsRef"] is None
    assert host_results["hostLogsCaptureError"] == (
        "artifact publication failed: OSError"
    )
    assert "hostLogs" not in host_results


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_generic_realizer_publishes_host_logs_from_failed_host_realization(cleanup_fails) -> (
    None
):
    """Logs captured when the runtime removes an unattested host are still published.

    ``GenericOmnigentHostRuntime.realize`` removes the container itself when
    registration or attestation fails and carries the cleanup evidence on the
    failure. The realizer's own cleanup then finds no container; the earlier
    capture must reach the single publication point instead of being lost.
    """

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer

    class Artifacts:
        def __init__(self) -> None:
            self.text_writes: list[dict[str, object]] = []
            self.json_writes: list[dict[str, object]] = []

        async def write_text(self, **kwargs):
            self.text_writes.append(kwargs)
            return "artifact:host-logs-realize-1"

        async def write_json(self, **kwargs):
            self.json_writes.append(kwargs)
            return "artifact:cleanup-realize-1"

    artifacts = Artifacts()
    realizer._artifacts = artifacts

    class RegistrationFailed(RuntimeError):
        pass

    async def realize_fails(**kwargs):
        # Production persists the deterministic cleanup authority before the
        # first launch mutation, then removes the host on the failure path.
        await kwargs["authority_sink"](
            {
                "kind": "host",
                "containerName": "mm-host-1",
                "stateVolumeRef": "mm-state-1",
                "controlVolumeRef": None,
            }
        )
        harness.events.append("host-realize-failed")
        failure = RegistrationFailed("host never registered")
        failure.host_cleanup_evidence = {
            "containerRemoved": True,
            "hostLogs": "runner: registration timed out\nopencode: exited 1\n",
            "hostLogsTruncated": False,
        }
        raise failure

    async def cleanup_without_container(**_kwargs):
        harness.events.append("host-cleaned")
        if cleanup_fails:
            raise RuntimeError("proxy unavailable after host removal")
        return {"containerRemoved": True}

    realizer._host_runtime.realize = realize_fails
    realizer._host_runtime.cleanup = cleanup_without_container

    with pytest.raises(RegistrationFailed):
        await realizer.execute(harness.publish_request, _plan("opencode-go/model"))

    assert "host-cleaned" in harness.events
    assert [write["name"] for write in artifacts.text_writes] == [
        "generic-host-logs.txt"
    ]
    assert artifacts.text_writes[0]["link_type"] == "evidence.host_logs"
    assert artifacts.text_writes[0]["payload"].startswith(
        "runner: registration timed out"
    )
    if cleanup_fails:
        assert not any(write["name"] == "generic-host-cleanup.json" for write in artifacts.json_writes)
        return
    host_results = next(
        write["payload"]
        for write in artifacts.json_writes
        if write["name"] == "generic-host-cleanup.json"
    )["results"]["host"]
    assert host_results["hostLogsRef"] == "artifact:host-logs-realize-1"
    assert host_results["containerRemoved"] is True
    assert "hostLogs" not in host_results


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_generic_host_runtime_carries_cleanup_evidence_on_failed_realization(cleanup_fails) -> (
    None
):
    """The runtime's own failure cleanup keeps the host log tail on the failure."""

    from moonmind.omnigent.host_runtime import (
        GenericOmnigentHostRuntime,
        PreparedHostInputs,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    plan = _plan("opencode-go/model")
    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )
    launch_policy = get_launch_policy("omnigent-on-demand@1")
    cleanup_calls: list[dict[str, object]] = []

    class Cleanup:
        async def cleanup(self, **kwargs):
            cleanup_calls.append(dict(kwargs))
            if cleanup_fails:
                raise RuntimeError("cleanup unavailable")
            return {
                "containerRemoved": True,
                "hostLogs": "runner: registration never completed\n",
                "hostLogsTruncated": False,
            }

    class Launcher:
        server_url = "http://omnigent:8080"

        async def launch(self, **_kwargs):
            return {
                "containerName": "mm-host-realize-1",
                "stateVolumeRef": "mm-state-realize-1",
                "controlVolumeRef": None,
                "hostCleanupRef": "host-cleanup:realize-1",
                "stateCleanupRef": "state-cleanup:realize-1",
            }

    class Registration:
        async def wait_for_registration(self, **_kwargs):
            raise TimeoutError("host never registered")

    class RuntimeEnvironment:
        def build(self, **_kwargs):
            return {}

    class Unused:
        """Port that ``realize`` does not reach before the injected failure."""

    runtime = GenericOmnigentHostRuntime(
        launcher=Launcher(),
        workspace_service=Unused(),
        skill_service=Unused(),
        tool_service=Unused(),
        github_credential_service=Unused(),
        egress_service=Unused(),
        runtime_environment_service=RuntimeEnvironment(),
        registration_waiter=Registration(),
        host_attestor=Unused(),
        cleanup_service=Cleanup(),
    )
    prepared = PreparedHostInputs(
        workspace_attachment={
            "kind": "bind",
            "sourceRef": "/tmp/work",
            "targetPath": "/workspaces/run",
            "accessMode": "read-write",
        },
        skill_attachment={
            "kind": "bind",
            "sourceRef": "/tmp/skills",
            "targetPath": "/opt/moonmind-skills",
            "accessMode": "read-only",
        },
        tool_attachments=(),
        egress_attestation={
            "networkRef": "egress",
            "profileRef": "egress-profile:default",
            "profileDigest": "sha256:" + "a" * 64,
            "appliedRuleDigest": "sha256:" + "b" * 64,
            "attestationRef": "artifact://egress",
        },
    )

    with pytest.raises(TimeoutError) as excinfo:
        await runtime.realize(
            request=AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="workflow-realize-fails",
                idempotencyKey="idem-realize-fails",
            ),
            plan=plan,
            runtime_binding_id="omnigent-runtime-binding:realize-1",
            host_lease_ref="omnigent-host-lease:sha256:" + "c" * 64,
            host_lease_generation=1,
            host_class=host_class,
            launch_policy=launch_policy,
            prepared=prepared,
            credential_handles=[],
        )

    assert [call["container_name"] for call in cleanup_calls] == ["mm-host-realize-1"]
    if cleanup_fails:
        assert excinfo.value.host_cleanup_error == "RuntimeError"
        return
    evidence = excinfo.value.host_cleanup_evidence
    assert evidence["containerRemoved"] is True
    assert evidence["hostLogs"].startswith("runner: registration never completed")


@pytest.mark.asyncio
async def test_generic_realizer_accepts_remotely_verified_no_commit_publication() -> (
    None
):
    """A verified no-commit step is a terminal outcome, not a dispatch failure.

    Publication steps such as a pull-request handoff legitimately add no
    commits because earlier steps already pushed the work. The durable
    workflow -- not this realizer -- owns whether that satisfies the step's
    publish contract.
    """

    harness = await _generic_publication_harness(_NO_COMMIT_PUBLICATION)

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.failure_class is None
    assert result.provider_error_code is None
    assert result.metadata["push_status"] == "no_commits"
    assert result.metadata["acceptedRepositoryEvidence"] == {
        "schemaVersion": "accepted-repository-evidence/v1",
        "pushStatus": "no_commits",
        "branch": "moonmind-job-test",
        "baseBranch": "moonmind-job-test",
        "headSha": "b" * 40,
        "commitsAheadOfBase": 0,
        "repositoryChanged": False,
        "publicationAuthorized": True,
        "candidateContaminated": False,
        "remoteVerified": True,
        "authority": "omnigent.generic_host_execution",
    }
    # Terminal success must still release every fenced authority in order.
    assert harness.events[-1] == "command-settled:applied"
    assert harness.events.index("workspace-published") < harness.events.index(
        "host-cleaned"
    )


@pytest.mark.asyncio
async def test_generic_realizer_rejects_unverified_repository_publication() -> None:
    """Only remotely verified publication outcomes may release the workspace."""

    harness = await _generic_publication_harness({"push_status": "skipped"})

    with pytest.raises(HarnessPlatformError) as excinfo:
        await harness.realizer.execute(
            harness.publish_request, _plan("opencode-go/model")
        )

    assert excinfo.value.code == "OMNIGENT_REPOSITORY_OUTPUT_MISSING"


@pytest.mark.asyncio
async def test_provider_leases_acquire_sorted_and_release_reverse_order() -> None:
    profiles = {
        "profile-a": SimpleNamespace(
            enabled=True,
            auth_state="connected",
            runtime_id="opencode",
            capacity_scope_ref="provider-profile:profile-a",
            credential_generation=3,
        ),
        "profile-z": SimpleNamespace(
            enabled=True,
            auth_state="connected",
            runtime_id="opencode",
            capacity_scope_ref="provider-profile:profile-z",
            credential_generation=9,
        ),
    }
    events: list[str] = []

    class LeaseClient:
        async def acquire_execution_lease(self, **kwargs):
            profile_id = kwargs["profile_id"]
            events.append(f"acquire:{profile_id}")
            return CredentialLease(
                profile_id=profile_id,
                runtime_id=kwargs["runtime_id"],
                lease_id=f"lease-{profile_id}",
                owner_id=kwargs["owner_id"],
                purpose=kwargs["purpose"],
            )

        async def inspect_lease(self, lease):
            events.append(f"inspect:{lease.profile_id}")
            return {"active": True}

        async def release_lease(self, lease):
            events.append(f"release:{lease.profile_id}")

    base = _plan("opencode-go/model").payload.model_dump(by_alias=True, mode="json")
    base["credentialBindings"] = {
        "z-slot": {
            "providerProfileRef": "profile-z",
            "materializerRef": "opencode-auth-json@1",
        },
        "a-slot": {
            "providerProfileRef": "profile-a",
            "materializerRef": "opencode-auth-json@1",
        },
    }
    plan = create_execution_plan_envelope(base)
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(profiles), lease_client=LeaseClient()
    )

    acquired = await coordinator.acquire_all(
        plan=plan,
        workflow_id="workflow",
        step_execution_id="step",
        idempotency_key="idem",
    )
    await coordinator.release_all(acquired)

    assert [(item.slot, item.credential_generation) for item in acquired] == [
        ("a-slot", 3),
        ("z-slot", 9),
    ]
    assert events == [
        "acquire:profile-a",
        "inspect:profile-a",
        "acquire:profile-z",
        "inspect:profile-z",
        "release:profile-z",
        "release:profile-a",
    ]


@pytest.mark.asyncio
async def test_secret_resolution_is_role_scoped_and_generation_fenced() -> None:
    profile = SimpleNamespace(
        credential_generation=4,
        secret_refs={
            "opencode_api_key": "env://OPENCODE_TEST_KEY",
            "unrelated_secret": "env://MUST_NOT_BE_READ",
        },
    )
    resolved_roles: list[str] = []

    class Resolver:
        async def resolve(self, ref):
            resolved_roles.append(str(ref))
            return "scoped-value"

    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="profile-a",
        capacity_scope_ref="provider-profile:profile-a",
        provider_lease_ref="provider-profile-lease:lease-a",
        credential_generation=4,
        lease=CredentialLease(
            profile_id="profile-a",
            runtime_id="opencode",
            lease_id="lease-a",
            owner_id="owner-a",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    service = OmnigentSecretResolutionService(
        session_factory=_session_factory({"profile-a": profile}),
        resolver=Resolver(),
    )

    bundle = await service.resolve(
        acquired=acquired, allowed_secret_roles=["opencode_api_key"]
    )
    assert bundle.values == {"opencode_api_key": "scoped-value"}
    assert len(resolved_roles) == 1
    assert "MUST_NOT_BE_READ" not in resolved_roles[0]

    profile.credential_generation = 5
    with pytest.raises(HarnessPlatformError) as exc:
        await service.resolve(
            acquired=acquired, allowed_secret_roles=["opencode_api_key"]
        )
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED.value
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_closed", [True, False, None])
async def test_janitor_recovers_binding_that_crashed_before_host_lease(owner_closed) -> None:
    bindings = InMemoryStableRuntimeBindingStore()
    binding = await bindings.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="credential-only-crash",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "profile-a",
                "providerLeaseRef": "provider-profile-lease:lease-a",
                "credentialGeneration": 4,
                "credentialRuntimeRef": "credential-runtime:sha256:" + "2" * 64,
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    calls: list[tuple[str, str]] = []

    class Realizer:
        async def reconcile(self, plan_ref, binding_id):
            calls.append((plan_ref, binding_id))

    janitor = GenericOmnigentHostJanitor(
        host_leases=InMemoryOmnigentHostLeaseRepository(),
        runtime_bindings=bindings,
        realizer=Realizer(),
        owner_has_closed=AsyncMock(return_value=owner_closed),
        stale_after_seconds=-1,
    )

    result = await janitor.run()

    assert calls == ([(binding.executionPlanRef, binding.bindingId)] if owner_closed is True else [])
    assert result["runtimeBindingsExamined"] == 1
    assert result["reconciled"] == int(owner_closed is True)
    assert result["conflicts"] == int(owner_closed is not True)


@pytest.mark.asyncio
async def test_tool_delivery_uses_plan_names_and_image_owned_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MoonLadderStudios/MoonMind#4558: no tools volume is inspected or
    # required. Retired volume settings cannot change tool selection.
    monkeypatch.setenv("MOONMIND_OMNIGENT_TOOLS_VOLUME_REF", "stale-volume")
    monkeypatch.setenv("OMNIGENT_GH_VERSION", "0.0.0")
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class Backend:
        async def run(self, argv, **_kwargs):  # pragma: no cover - must stay unused
            raise AssertionError(f"image delivery must not run Docker: {argv}")

    service = OmnigentMountedToolService(
        backend=Backend(),
        manifest_path=manifest,
        volume_ref="stale-volume",
    )
    result = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]},
        image_ref="example/host@sha256:" + "b" * 64,
    )

    assert result[0]["kind"] == "image"
    assert result[0]["sourceRef"] == "image:example/host@sha256:" + "b" * 64
    assert result[0]["targetPath"] == "/opt/moonmind-tools"
    assert result[0]["accessMode"] == "read-only"
    assert result[0]["tools"][0]["executableDigests"] == ["a" * 64]
    assert result[0]["tools"][0]["versionProbe"] == ["--version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", [None, [], "--help", [None], [""]])
async def test_tool_delivery_rejects_invalid_probe_before_docker(
    tmp_path: Path,
    probe: object,
) -> None:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps({"tools": [{"name": "docker", "versionProbe": probe}]}),
        encoding="utf-8",
    )
    backend = SimpleNamespace(run=AsyncMock())
    service = OmnigentMountedToolService(backend=backend, manifest_path=manifest)

    with pytest.raises(HarnessPlatformError, match="probe is malformed"):
        await service.materialize(
            {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["docker"]}
        )

    backend.run.assert_not_awaited()


def test_deployment_mounted_tool_names_come_from_locked_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps({"tools": [{"name": "GH"}, {"name": ""}]}),
        encoding="utf-8",
    )

    assert deployment_mounted_tool_names(manifest) == ("gh",)


def _signed_grant_digest(workspace_id: str, *, secret: str) -> str:
    """Issue the fixture grant HMAC for the requesting workflow-1/idem-1."""

    from moonmind.omnigent.workspace_sources import issue_existing_workspace_grant

    return issue_existing_workspace_grant(
        workspace_id=workspace_id,
        owner_workflow_id="workflow-1",
        owner_step_execution_id="idem-1",
        grantee_workflow_id="workflow-1",
        mode="read_only",
        generation=1,
        secret=secret,
    ).grant_digest or ""


def _grant_expires_at() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()


@pytest.mark.asyncio
async def test_workspace_attachment_mounts_the_deployment_volume_subpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "worker"
    workspace_root.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")

    async def runner(argv):
        raise AssertionError(
            "a remote daemon mount must not depend on an inspected volume "
            f"mountpoint: {argv}"
        )

    # MoonLadderStudios/MoonMind#4014 removed raw workspacePath precedence:
    # an existing workspace needs a server-issued ownership/use grant. The
    # daemon translation below exercises the granted path, not a raw mount.
    # Newly authored grants must carry an HMAC issuance signature bound to
    # the target workflow, so the fixture issues a signed grant.
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", "test-workspace-grant-secret")
    workspace_id = "granted-ws-1"
    granted = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    granted.mkdir(parents=True)
    (granted / "KEEP").write_text("kept", encoding="utf-8")
    SandboxWorkspaceRecordStore(workspace_root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id="workflow-1",
            step_execution_id="idem-1",
            relative_path="repo",
        )
    )

    service = OmnigentWorkspaceMaterializer(
        command_runner=runner,
        workspace_root=workspace_root,
        workspace_volume="agent-workspaces",
    )
    request = _request().model_copy(
        update={
            "workspace_spec": {
                "workspaceSource": {
                    "kind": "existing_workspace",
                    "existingWorkspaceGrant": {
                        "workspaceId": workspace_id,
                        "ownerWorkflowId": "workflow-1",
                        "ownerStepExecutionId": "idem-1",
                        "generation": 1,
                        # Exclusive use cannot be honored on a remote
                        # daemon view; read-only sharing is supported
                        # there through the qualified locator mapping.
                        "mode": "read_only",
                        "grantDigest": _signed_grant_digest(
                            workspace_id,
                            secret="test-workspace-grant-secret",
                        ),
                        "expiresAt": _grant_expires_at(),
                    },
                }
            }
        }
    )

    attachment = await service.materialize(request)

    # The daemon does not share the worker filesystem, and the volume
    # mountpoint it reports is not reachable as a bind source on every
    # supported daemon. Docker resolves the volume itself.
    assert attachment["kind"] == "volume"
    assert attachment["sourceRef"] == "agent-workspaces"
    assert attachment["subPath"] == "temporal_sandbox/granted-ws-1/repo"
    assert attachment["accessMode"] == "read-only"

    read_only_attachment = await service.materialize(request, mutation="read_only")

    assert read_only_attachment["kind"] == "volume"
    assert read_only_attachment["sourceRef"] == "agent-workspaces"
    assert read_only_attachment["subPath"] == "temporal_sandbox/granted-ws-1/repo"
    assert read_only_attachment["accessMode"] == "read-only"


def test_daemon_attachment_source_infers_remote_from_daemon_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset daemon mode with a configured daemon root must use volume subpath.

    Matches resolve_workspace_backend() and daemon_visible_workspace_path(),
    which infer remote mode when WORKFLOW_DOCKER_DAEMON_MODE is unset but
    WORKFLOW_WORKSPACE_DAEMON_ROOT is configured. Returning a worker-only
    bind here would recreate the empty-workspace failure on such deployments.
    """
    from moonmind.omnigent.host_services.workspace import (
        resolve_daemon_attachment_source,
    )

    workspace_root = tmp_path / "worker"
    candidate = workspace_root / "temporal_sandbox" / "ws-1" / "repo"
    candidate.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.delenv("WORKFLOW_DOCKER_DAEMON_MODE", raising=False)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "/daemon/root")

    attachment = resolve_daemon_attachment_source(
        workspace_volume="agent-workspaces",
        path=candidate,
    )

    assert attachment["kind"] == "volume"
    assert attachment["sourceRef"] == "agent-workspaces"
    assert attachment["subPath"] == "temporal_sandbox/ws-1/repo"


@pytest.mark.asyncio
async def test_daemon_workspace_root_infers_remote_from_daemon_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset daemon mode with a configured daemon root must inspect the volume."""
    from moonmind.omnigent.host_services.workspace import (
        resolve_daemon_workspace_root,
    )

    monkeypatch.delenv("WORKFLOW_DOCKER_DAEMON_MODE", raising=False)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "/daemon/root")

    async def runner(argv, input_bytes=None):
        assert argv[:3] == ["docker", "volume", "inspect"]
        return (0, "/var/lib/docker/volumes/agent-workspaces/_data\n", "")

    resolved = await resolve_daemon_workspace_root(
        runner=runner,
        workspace_volume="agent-workspaces",
    )

    assert resolved == Path("/var/lib/docker/volumes/agent-workspaces/_data")


@pytest.mark.asyncio
async def test_ready_host_retry_preserves_materialized_input_paths(monkeypatch):
    """Revoke the first Activity after host readiness, before sending its turn."""
    from moonmind.omnigent.execute import (
        _build_omnigent_first_message,
        _first_message_text,
    )

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan('opencode-go/model')
    paths = {'gateResultPath': '.moonmind/attachments/gate',
             'remainingWorkPath': '.moonmind/attachments/gate'}
    original_realize = harness.realizer._host_runtime.realize

    async def realized(**kwargs):
        return {**await original_realize(**kwargs), 'materializedInputPaths': paths}

    harness.realizer._host_runtime.realize = realized
    original_drive = harness.realizer._drive_session
    harness.realizer._drive_session = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr('moonmind.omnigent.activity_ownership.delivery_was_revoked', lambda: True)
    with pytest.raises(asyncio.CancelledError):
        await harness.realizer._execute_lifecycle(harness.publish_request, plan)
    assert 'host-cleaned' not in harness.events
    harness.realizer._drive_session = original_drive
    original_session = harness.realizer._session_driver

    async def resumed(request, **kwargs):
        message = await _build_omnigent_first_message(request=request, prompt={'text': 'Remediate'}, artifact_gateway=None)
        text = _first_message_text(message)
        for name, path in paths.items():
            assert f'- {name}: {path}' in text
        return await original_session(request, **kwargs)

    harness.realizer._session_driver = resumed
    result = await harness.realizer._execute_lifecycle(harness.publish_request, plan)
    assert result.summary == 'done'
    assert harness.events.count('host-ready') == 1


@pytest.mark.asyncio
async def test_skill_attachment_mounts_the_deployment_volume_subpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote daemon receives the volume and subpath, not a host path.

    The daemon's reported volume mountpoint is not reachable as a bind source
    on every supported daemon; Docker creates the missing directory instead of
    refusing it, so the host would mount an empty projection whose directory
    still passes ``test -d``.
    """

    workspace_root = tmp_path / "worker"
    workspace_root.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")

    resolved_skillset = {
        "snapshot_id": "skillset_abc",
        "resolved_at": "2026-01-01T00:00:00+00:00",
        "skills": [],
    }

    class Gateway:
        async def read_bytes(self, artifact_id: str) -> bytes:
            assert artifact_id == "art_skillset"
            return json.dumps(resolved_skillset).encode("utf-8")

    service = OmnigentSkillDeliveryService(
        workspace_root=workspace_root,
        workspace_volume="agent-workspaces",
        artifact_gateway=Gateway(),
    )

    attachment = await service.anticipated_attachment(
        {
            "resolvedSkillSetRef": "art_skillset",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:1",
        },
        owner_ref="idem-1",
    )

    assert attachment["kind"] == "volume"
    assert attachment["sourceRef"] == "agent-workspaces"
    assert attachment["subPath"].startswith(".skill-projections/")
    assert attachment["subPath"].endswith("/runtime/skills_active/skillset_abc")
    assert attachment["targetPath"] == "/opt/moonmind-skills"
    assert attachment["accessMode"] == "read-only"
    # Cleanup stays worker-owned: the projection is removed through the
    # worker filesystem, never through the daemon's view of the volume.
    assert attachment["cleanupSourceRef"].startswith(str(workspace_root))


@pytest.mark.asyncio
async def test_launch_renders_volume_subpath_mounts_and_rejects_escapes() -> None:
    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append(list(argv))
            return (0, "container-id" if argv[1] == "create" else "", "")

    class Scripts:
        def build_entrypoint(self, **kwargs):
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

    def _spec(skill_subpath: str) -> HostLaunchSpec:
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
                "correlationName": "mm-host-subpath",
                "workspaceAttachment": {
                    "kind": "volume",
                    "sourceRef": "agent-workspaces",
                    "subPath": "temporal_sandbox/ws-1/repo",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                "skillAttachment": {
                    "kind": "volume",
                    "sourceRef": "agent-workspaces",
                    "subPath": skill_subpath,
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                },
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
        spec=_spec(".skill-projections/key/runtime/skills_active/snap"),
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )

    create = next(argv for argv in calls if argv[:2] == ["docker", "create"])
    mounts = [
        create[index + 1] for index, value in enumerate(create) if value == "--mount"
    ]
    assert (
        "type=volume,src=agent-workspaces,dst=/workspaces/run,"
        "volume-subpath=temporal_sandbox/ws-1/repo"
    ) in mounts
    assert (
        "type=volume,src=agent-workspaces,dst=/opt/moonmind-skills,"
        "volume-subpath=.skill-projections/key/runtime/skills_active/snap,readonly"
    ) in mounts
    # The state volume has no subpath and keeps the whole-volume mount.
    assert "type=volume,src=mm-host-state-test,dst=/home/app/.omnigent" in mounts

    for escape in ("../outside", "/absolute", "with,comma"):
        with pytest.raises(HarnessPlatformError) as exc:
            await launcher.launch(
                spec=_spec(escape),
                host_class=host_class,
                launch_policy=get_launch_policy("omnigent-on-demand@1"),
                credential_handles=[],
            )
        assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED


def test_skill_projection_probe_names_the_failed_invariant() -> None:
    """The probe's four invariants must not collapse into one sentence."""

    message = _skill_projection_probe_message(
        "exact runner environment",
        "moonmind-skill-projection-check:projection-manifest-missing\n",
        "",
        "/opt/moonmind-skills",
    )
    assert "projection-manifest-missing" in message
    assert "_manifest.json" in message
    assert "/opt/moonmind-skills" in message

    env_message = _skill_projection_probe_message(
        "OpenCode shell environment",
        "moonmind-skill-projection-check:step-execution-id-mismatch\n",
        "",
        "/opt/moonmind-skills",
    )
    assert "MOONMIND_STEP_EXECUTION_ID" in env_message
    assert "OpenCode shell environment" in env_message

    # A probe that could not report a reason carries its own diagnostics
    # instead of a sentence that names none.
    transport = _skill_projection_probe_message(
        "exact runner environment",
        "",
        "Error response from daemon: container not running",
        "/opt/moonmind-skills",
    )
    assert "container not running" in transport


_AMBIENT_GITHUB_TOKEN = "ambientTokenA"


def _ambient_git_environment(home: Path, path: str) -> dict[str, str]:
    """A caller environment carrying token A through every ambient Git route."""
    home.mkdir(parents=True, exist_ok=True)
    (home / ".netrc").write_text(
        f"machine github.com login x-access-token password {_AMBIENT_GITHUB_TOKEN}\n"
    )
    (home / ".gitconfig").write_text(
        f"[http]\n\textraHeader = X-Ambient: {_AMBIENT_GITHUB_TOKEN}\n"
        '[credential]\n\thelper = "!f() { echo username=x-access-token; '
        f'echo password={_AMBIENT_GITHUB_TOKEN}; }}; f"\n'
    )
    return {
        "PATH": path,
        "HOME": str(home),
        "GH_TOKEN": _AMBIENT_GITHUB_TOKEN,
        "GITHUB_TOKEN": _AMBIENT_GITHUB_TOKEN,
        "GIT_CONFIG_PARAMETERS": (
            f"'http.extraheader'='Authorization: bearer {_AMBIENT_GITHUB_TOKEN}'"
        ),
    }


@pytest.mark.parametrize("selected", [True, False], ids=["selected-B", "anonymous"])
def test_sandbox_clone_script_sends_only_admitted_credential_over_real_git(
    tmp_path, selected
) -> None:
    """#4011: the production clone script, run by real git, never sends ambient A."""
    from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv
    from tests.helpers.git_transport import (
        basic_authorization,
        start_synthetic_github,
        write_proxy_git_shim,
    )

    selected_token = "selectedTokenB"
    argv = build_daemon_git_clone_argv(
        volume="agent_workspaces",
        target_in_volume="run/repo",
        source="https://github.com/owner/repo.git",
        branch="main",
        image="alpine/git:v2.43.0",
        git_user_name="MoonMind",
        git_user_email="moonmind@example.invalid",
        authenticated=selected,
    )
    script = argv[argv.index("-ceu") + 1]
    positional = argv[argv.index("--") + 1 :]
    target = tmp_path / "work" / "repo"
    target.parent.mkdir()
    transport_gen = start_synthetic_github(
        tmp_path / "transport", required_token=selected_token if selected else None
    )
    transport = next(transport_gen)
    try:
        shim = write_proxy_git_shim(tmp_path, transport)
        environment = _ambient_git_environment(
            tmp_path / "home", f"{shim}:{os.environ['PATH']}"
        )
        result = subprocess.run(
            [
                "/bin/sh",
                "-ceu",
                script,
                "--",
                positional[0],
                positional[1],
                str(target),
                *positional[3:],
            ],
            input=selected_token.encode() if selected else b"",
            env=environment,
            capture_output=True,
            check=False,
        )
    finally:
        transport_gen.close()

    assert result.returncode == 0, result.stderr.decode()
    assert (target / "README.md").read_text() == "synthetic transport\n"
    assert transport.requests
    assert not any(
        _AMBIENT_GITHUB_TOKEN in value for value in transport.header_values()
    )
    expected = {basic_authorization(selected_token)} if selected else set()
    assert set(transport.sent_authorizations()) == expected
    git_config = (target / ".git" / "config").read_text()
    assert selected_token not in git_config
    assert "https://github.com/owner/repo.git" in git_config


def test_generic_host_gh_reader_observes_same_owner_refresh_over_ambient_token(
    monkeypatch, tmp_path
) -> None:
    """#4011: the running host's gh reads the live projection, not a start copy."""
    import shutil

    from moonmind.omnigent.host_services.runtime_scripts import _RUNTIME_BIN_DIR

    gh = shutil.which("gh") or "/opt/moonmind-tools/bin/gh"
    if not Path(gh).exists():
        pytest.skip("requires the GitHub CLI binary")
    gh = os.path.realpath(gh)
    script, _environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[],
        skill_attachment={"targetPath": "/opt/moonmind-skills"},
        step_execution_id="workflow:run:node-1:execution:1",
        github_credential_attachment={
            "targetPath": "/run/mm-credentials/github",
            "githubHost": "github.com",
        },
    )
    start = script.index("if [ -d /run/mm-credentials/github ]; then ")
    end = script.index(f"chmod 0700 {_RUNTIME_BIN_DIR}/gh; fi; ") + len(
        f"chmod 0700 {_RUNTIME_BIN_DIR}/gh; fi; "
    )
    mount = tmp_path / "mount"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    fragment = (
        script[start:end]
        .replace("/run/mm-credentials/github", str(mount))
        .replace(_RUNTIME_BIN_DIR, str(bin_dir))
        .replace("/home/app", str(home))
        .replace("/opt/moonmind-tools/bin/gh", gh)
    )

    first = _ProjectionBackend(existing=False)
    asyncio.run(_materialize_projection(monkeypatch, first, "selected-token-B1"))
    subprocess.run(
        _projection_writer(first, mount), input=b"selected-token-B1", check=True
    )
    subprocess.run(["/bin/sh", "-ceu", fragment], check=True)

    def host_gh_token() -> str:
        # Ambient A is present in the agent environment; the generated wrapper
        # must still resolve the admitted projection.
        result = subprocess.run(
            [str(bin_dir / "gh"), "auth", "token"],
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "GH_TOKEN": _AMBIENT_GITHUB_TOKEN,
                "GITHUB_TOKEN": _AMBIENT_GITHUB_TOKEN,
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    assert host_gh_token() == "selected-token-B1"

    refresh = _ProjectionBackend(existing=True)
    asyncio.run(_materialize_projection(monkeypatch, refresh, "selected-token-B2"))
    subprocess.run(
        _projection_writer(refresh, mount), input=b"selected-token-B2", check=True
    )
    # No entrypoint rerun: the live reader sees the complete new issuance.
    assert host_gh_token() == "selected-token-B2"
