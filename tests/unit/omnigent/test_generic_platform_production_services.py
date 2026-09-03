from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.auth.github_credentials import (
    GitHubCredentialSource,
    ResolvedGitHubCredential,
)
from moonmind.omnigent.credential_materializers import (
    CredentialMaterializationContext,
    CredentialRuntimeHandle,
    DockerOmnigentProviderConfigMaterializer,
    DockerOpencodeAuthJsonMaterializer,
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
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
    OmnigentPlannedHostResolver,
    _ref,
)
from moonmind.omnigent.harness_platform.stores import (
    ExecutionPlanUsageIdentity,
    InMemoryExecutionPlanStore,
    InMemoryExecutionPlanUsageStore,
)
from moonmind.omnigent.host_leases import InMemoryOmnigentHostLeaseRepository
from moonmind.omnigent.host_services.attestation import (
    _assert_exact_omnigent_build,
    _attest_workspace_mount,
    _read_exact_host_model_options,
    _run_exact_host_opencode_command,
    _run_exact_host_runner_command,
)
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
    github_repository_from_request,
)
from moonmind.omnigent.host_ports import HostLaunchSpec
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.mounted_tools import (
    OmnigentMountedToolService,
    deployment_mounted_tool_names,
)
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
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
from moonmind.security.execution_fanout_capabilities import (
    verify_execution_fanout_capability,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


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
    assert "from omnigent.opencode_native_app_server import filtered_server_env" in argv[5]
    assert "moonmind-opencode-context" in argv[5]
    assert "env=runner_env" in argv[5]
    assert argv[6:] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert kwargs == {"timeout_seconds": 30.0, "check": False}


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


def _evidence_provider(*, validated_at: datetime | None = None) -> SimpleNamespace:
    image_ref = "ghcr.io/moonmind/opencode@sha256:" + "a" * 64
    return SimpleNamespace(
        credential_generation=4,
        model_catalog_evidence_json={
            "credentialGeneration": 4,
            "imageRef": image_ref,
            "models": [{"qualifiedId": "opencode-go/model"}],
            "validatedAt": (validated_at or datetime.now(UTC)).isoformat(),
        },
    )


def test_planning_model_evidence_is_bound_to_generation_and_host_image() -> None:
    image_ref = "ghcr.io/moonmind/opencode@sha256:" + "a" * 64
    provider = _evidence_provider()
    OmnigentExecutionPlanningService._verify_model_evidence(
        provider,
        "opencode-go/model",
        expected_image_ref=image_ref,
    )

    provider.model_catalog_evidence_json["credentialGeneration"] = 3
    with pytest.raises(HarnessPlatformError) as stale:
        OmnigentExecutionPlanningService._verify_model_evidence(
            provider,
            "opencode-go/model",
            expected_image_ref=image_ref,
        )
    assert stale.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE

    provider.model_catalog_evidence_json["credentialGeneration"] = 4
    with pytest.raises(HarnessPlatformError) as wrong_image:
        OmnigentExecutionPlanningService._verify_model_evidence(
            provider,
            "opencode-go/model",
            expected_image_ref="ghcr.io/moonmind/opencode@sha256:" + "b" * 64,
        )
    assert wrong_image.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE


def test_planning_rejects_a_model_catalog_older_than_the_refresh_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity alone would launch from the first catalog ever observed.

    A healthy deployment never changes its credential generation or host image
    digest on its own, so pre-session planning has to enforce the same
    observation interval the bootstrap reconciler refreshes on. Otherwise it
    keeps admitting -- and launching -- a model the provider may have removed.
    """

    # Exercise the documented default: the interval applies with no env value.
    monkeypatch.delenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", raising=False)
    image_ref = "ghcr.io/moonmind/opencode@sha256:" + "a" * 64
    expired = _evidence_provider(validated_at=datetime.now(UTC) - timedelta(hours=9))

    with pytest.raises(HarnessPlatformError) as stale:
        OmnigentExecutionPlanningService._verify_model_evidence(
            expired,
            "opencode-go/model",
            expected_image_ref=image_ref,
        )
    assert stale.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE
    assert "refresh interval" in str(stale.value)

    # The interval is deployment-configurable, and ``0`` restores identity-only
    # staleness at this boundary exactly as it does at the reconciler.
    monkeypatch.setenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", "0")
    OmnigentExecutionPlanningService._verify_model_evidence(
        expired,
        "opencode-go/model",
        expected_image_ref=image_ref,
    )

    # An observation that cannot say when it was taken is never admitted.
    monkeypatch.delenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS")
    undated = _evidence_provider()
    undated.model_catalog_evidence_json.pop("validatedAt")
    with pytest.raises(HarnessPlatformError) as undatable:
        OmnigentExecutionPlanningService._verify_model_evidence(
            undated,
            "opencode-go/model",
            expected_image_ref=image_ref,
        )
    assert undatable.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE


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


@pytest.mark.asyncio
async def test_planned_host_resolver_uses_exact_launch_artifact() -> None:
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
    current_host = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/current@sha256:" + "c" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": implementation.implementation_ref(),
                    "runtimeDependencies": [],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "readOnlyRoot": True,
                "restrictedEgress": True,
                "workspaceBind": True,
            },
            "runtime": {"uid": 2000, "gid": 2000, "home": "/home/app"},
        }
    )
    exact_image = "ghcr.io/example/admitted@sha256:" + "a" * 64
    launch = {
        "schemaVersion": 3,
        "launchPolicyRef": "omnigent-on-demand@1",
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

    class Selector:
        def select(self, **_kwargs):
            return current_host

    class Artifacts:
        async def read_bytes(self, ref: str) -> bytes:
            assert ref == "artifact:launch-1"
            return raw

    host, policy = await OmnigentPlannedHostResolver(
        catalog_repository=Catalogs(),
        host_class_selector=Selector(),
        artifact_gateway=Artifacts(),
    )(plan)

    assert host.imageRef == exact_image
    assert host.runtime["uid"] == 1000
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
async def test_github_credential_projection_transports_secret_only_on_stdin(
    monkeypatch,
) -> None:
    secret = "github-secret-that-must-not-be-inspectable"

    async def resolve(*, repo=None):
        assert repo == "MoonLadderStudios/Tactics"
        return ResolvedGitHubCredential(
            token=secret,
            source=GitHubCredentialSource.DIRECT_ENV,
            sourceName="GITHUB_TOKEN",
            repo=repo,
        )

    monkeypatch.setattr(
        "moonmind.omnigent.host_services.github_credentials.resolve_github_credential",
        resolve,
    )

    class Backend:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, argv, **kwargs):
            self.calls.append((list(argv), dict(kwargs)))
            if argv[1:3] == ["volume", "inspect"]:
                owner_ref = "lease-owner-1"
                return 0, hashlib.sha256(owner_ref.encode()).hexdigest()[:32], ""
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
    attachment = await service.materialize(
        request=request,
        resolved_tools={"tools": ["gh", "git"]},
        owner_ref="lease-owner-1",
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


@pytest.mark.asyncio
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
            "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN": "scoped-fanout-token",
        },
    )

    setup_runs = [argv for argv, _kwargs in calls if argv[:2] == ["docker", "run"]]
    assert len(setup_runs) == len(expected_secret_payloads) + 1
    for argv in setup_runs:
        assert argv[argv.index("--user") + 1] == "0:0"
    workload_create = next(argv for argv, _kwargs in calls if argv[:2] == ["docker", "create"])
    assert workload_create[workload_create.index("--user") + 1] == "1000:1000"
    assert "scoped-fanout-token" not in json.dumps(
        [argv for argv, _kwargs in calls]
    )
    assert [
        kwargs["input_bytes"]
        for _argv, kwargs in calls
        if kwargs.get("input_bytes")
    ] == expected_secret_payloads
    assert script_inputs["runtime_environment"] == {
        "MOONMIND_URL": "http://api:8000",
        "MOONMIND_AGENT_RUN_ID": "agent-run-1",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
        "MOONMIND_STEP_ID": "step-1",
        "MOONMIND_RUNTIME_ID": "opencode-native",
        "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE": (
            "/run/moonmind-host-auth/execution-fanout"
        ),
    }
    assert script_inputs["control_credential_available"] is bool(host_api_token)


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
    assert capability.parent_workflow_id == "workflow-1"
    assert capability.agent_run_id == "workflow-1:run-1:node-1:execution:1"
    assert capability.session_id == "host-lease-1"
    assert capability.runtime_id == "opencode-native"
    assert capability.source_kind == "omnigent"


@pytest.mark.asyncio
async def test_opencode_volume_materialization_transports_secret_only_on_stdin() -> (
    None
):
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
            provider_route_ref="opencode-go",
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
        "opencode-go": {"type": "api", "key": secret},
    }
    assert artifacts.payloads[0]["providerRouteRef"] == "opencode-go"
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
) -> SimpleNamespace:
    """Build the real generic-host realizer around one publication outcome."""

    events: list[str] = []

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

        async def heartbeat(self, lease_ref, **kwargs):
            self.heartbeat_count += 1
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

        async def cleanup_all(self, handles):
            assert handles == (handle,)
            events.append("credentials-cleaned")
            return ()

    class HostRuntime:
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
            }

        async def cleanup(self, **_kwargs):
            events.append("host-cleaned")
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
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
        await asyncio.sleep(0.03)
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
        runtime_store=runtime_store,
        host_leases=host_leases,
    )


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
            "Provider Profile capacity acquired.",
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
async def test_generic_realizer_publishes_host_logs_from_failed_host_realization() -> (
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
    host_results = next(
        write["payload"]
        for write in artifacts.json_writes
        if write["name"] == "generic-host-cleanup.json"
    )["results"]["host"]
    assert host_results["hostLogsRef"] == "artifact:host-logs-realize-1"
    assert host_results["containerRemoved"] is True
    assert "hostLogs" not in host_results


@pytest.mark.asyncio
async def test_generic_host_runtime_carries_cleanup_evidence_on_failed_realization() -> (
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
async def test_janitor_recovers_binding_that_crashed_before_host_lease() -> None:
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
        stale_after_seconds=-1,
    )

    result = await janitor.run()

    assert calls == [(binding.executionPlanRef, binding.bindingId)]
    assert result["runtimeBindingsExamined"] == 1
    assert result["reconciled"] == 1


@pytest.mark.asyncio
async def test_tool_delivery_uses_plan_names_and_deployment_owned_volume(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "gh",
                        "version": "2.76.2",
                        "path": "bin/gh",
                        "platforms": {"linux/amd64": {"executableSha256": "a" * 64}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **_kwargs):
            calls.append(list(argv))
            return b""

    service = OmnigentMountedToolService(
        backend=Backend(),
        manifest_path=manifest,
        volume_ref="deployment-tools",
    )
    result = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]}
    )

    assert calls == [["docker", "volume", "inspect", "deployment-tools"]]
    assert result[0]["sourceRef"] == "deployment-tools"
    assert result[0]["accessMode"] == "read-only"
    assert result[0]["tools"][0]["executableDigests"] == ["a" * 64]


def test_deployment_mounted_tool_names_come_from_locked_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps({"tools": [{"name": "GH"}, {"name": ""}]}),
        encoding="utf-8",
    )

    assert deployment_mounted_tool_names(manifest) == ("gh",)


@pytest.mark.asyncio
async def test_workspace_attachment_translates_to_daemon_visible_volume_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "worker"
    workspace = workspace_root / "run-1"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")

    async def runner(argv):
        assert argv[-1] == "agent-workspaces"
        return 0, "/daemon/agent_workspaces\n", ""

    service = OmnigentWorkspaceMaterializer(
        command_runner=runner,
        workspace_root=workspace_root,
        workspace_volume="agent-workspaces",
    )
    request = _request().model_copy(
        update={"workspace_spec": {"workspacePath": str(workspace)}}
    )

    attachment = await service.materialize(request)

    assert attachment["sourceRef"] == "/daemon/agent_workspaces/run-1"
    assert attachment["accessMode"] == "read-write"

    read_only_attachment = await service.materialize(request, mutation="read_only")

    assert read_only_attachment["sourceRef"] == "/daemon/agent_workspaces/run-1"
    assert read_only_attachment["accessMode"] == "read-only"
