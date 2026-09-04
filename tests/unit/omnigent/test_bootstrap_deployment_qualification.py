"""Deployment qualification must attest the combination admission compiles."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.omnigent_agent_profile_selection import (
    default_launch_policy_ref,
)
from api_service.services.omnigent_policies import bootstrap_document
from moonmind.omnigent.bootstrap import controller as controller_module
from moonmind.omnigent.bootstrap.models import (
    BootstrapDesired,
    BootstrapRecord,
    BootstrapResolved,
    BootstrapState,
    ResolvedOmnigentDeploymentState,
)
from moonmind.omnigent.bootstrap.opencode import resolve_model_by_display
from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot
from moonmind.omnigent.policies import compile_policy_snapshot

_SERVER_IMAGE_REF = "ghcr.io/example/omnigent-server@sha256:" + "a" * 64
_HOST_IMAGE_REF = "ghcr.io/example/omnigent-host-opencode@sha256:" + "7" * 64
_IMPLEMENTATION_DIGEST = "sha256:" + "c" * 64
# The deployment-managed OpenCode Agent Profile allows both the generic and the
# harness-shaped launch policy; admission always selects the first.
_ALLOWED_LAUNCH_POLICIES = ["omnigent-on-demand@1", "opencode-on-demand@1"]


def _profile_document() -> dict:
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": "default",
        "source": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1",
            "upstreamSnapshotDigest": "sha256:" + "d" * 64,
        },
        "harness": {
            "id": "opencode-native",
            "catalogRef": "omnigent-harness-catalog:sha256:" + "e" * 64,
            "implementationRef": (
                "omnigent-harness-implementation:" + _IMPLEMENTATION_DIGEST
            ),
        },
        "credentialSlots": [
            {
                "id": "primary-model",
                "acceptedAuthModels": ["own-auth"],
                "acceptedProviderIds": ["opencode-go"],
            }
        ],
        "model": {
            "qualifiedId": "opencode-go/muse-spark-1.2-contributor",
            "effort": "xhigh",
        },
        "workspace": {"mutation": "allowed"},
        "skills": [],
        "tools": [],
        "capture": {"stream": True, "evidence": True},
        "continuations": {"checkpoint": True, "branch": True},
        "publish": {"mode": "none"},
        "allowedLaunchPolicyRefs": list(_ALLOWED_LAUNCH_POLICIES),
    }


def _version_row(document: dict, *, version: int = 27) -> SimpleNamespace:
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return SimpleNamespace(
        profile_id="omnigent-opencode-default",
        version=version,
        digest=digest,
        document=document,
    )


class _Session:
    """Minimal read-only session for the qualification lookups."""

    def __init__(self, version: SimpleNamespace) -> None:
        self.version = version

    async def get(self, model, _identity):
        if model.__name__ == "OmnigentAgentProfile":
            return SimpleNamespace(
                profile_id="omnigent-opencode-default",
                active_version=self.version.version,
            )
        if model.__name__ == "ManagedAgentProviderProfile":
            return SimpleNamespace(
                profile_id=_identity,
                runtime_id="opencode",
                provider_id=(
                    "opencode" if _identity == "opencode-zen-free" else "opencode-go"
                ),
                credential_generation=1,
            )
        return None

    async def execute(self, _statement):
        version = self.version
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: version))


def _catalog_snapshot():
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.10.0",
        omnigentBuildDigest="sha256:" + "a" * 64,
        sourceDigest="sha256:" + "b" * 64,
        observedAt=datetime.now(UTC),
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": _IMPLEMENTATION_DIGEST,
                    "pluginEntryPoint": None,
                },
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                },
            }
        ],
    )


def _resolved_state() -> SimpleNamespace:
    return SimpleNamespace(
        opencode_host_image_ref=_HOST_IMAGE_REF,
        server_image_ref=_SERVER_IMAGE_REF,
        architecture="linux/amd64",
        details={
            "opencodeHostCompatibility": {
                "status": "ready",
                "failureCode": None,
                "serverImageRef": _SERVER_IMAGE_REF,
                "hostImageRef": _HOST_IMAGE_REF,
            }
        },
    )


@pytest.fixture
def qualification_boundary(monkeypatch, tmp_path):
    """Wire the qualification boundary to hermetic collaborators."""

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", _HOST_IMAGE_REF)
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )

    import api_service.db.base as db_base
    import moonmind.omnigent.bootstrap.evidence as evidence_module
    import moonmind.omnigent.bootstrap.qualification as qualification_module
    import moonmind.omnigent.bootstrap.store as store_module
    from moonmind.omnigent.harness_platform import catalog_service

    state = SimpleNamespace(session=_Session(_version_row(_profile_document())))

    @asynccontextmanager
    async def _session_scope():
        yield state.session

    monkeypatch.setattr(
        db_base, "async_session_maker", lambda: _session_scope(), raising=False
    )

    snapshot = _catalog_snapshot()

    class _Repo:
        def __init__(self, _factory) -> None:
            pass

        async def latest(self, _endpoint_ref):
            return SimpleNamespace(snapshot=snapshot)

    monkeypatch.setattr(catalog_service, "DbHarnessCatalogRepository", _Repo)

    async def _run_qualification(**_kwargs):
        return {
            "results": {"readQualification": "passed"},
            "evidenceRefs": {"readRun": "artifact:read-run"},
        }

    monkeypatch.setattr(qualification_module, "run_qualification", _run_qualification)
    monkeypatch.setattr(store_module, "load_resolved_state", _resolved_state)

    async def _resolve_runtime_snapshot(_service, policy_ref: str):
        policy_id, _, version_text = policy_ref.rpartition("@")
        return compile_policy_snapshot(
            policy_id=policy_id,
            version=int(version_text),
            document=bootstrap_document(
                host_mode="on_demand_docker",
                execution_profile_ref="omnigent-opencode@1",
                server_image_ref=_SERVER_IMAGE_REF,
                host_image_ref=_HOST_IMAGE_REF,
                harness="opencode-native",
                agent_identities=("opencode",),
                compatible_providers=("opencode-go", "opencode"),
            ),
            validation={"valid": True},
        )

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.OmnigentPolicyService."
        "resolve_runtime_snapshot",
        _resolve_runtime_snapshot,
    )
    monkeypatch.setattr(
        evidence_module,
        "write_deployment_evidence",
        lambda evidence, path=None: tmp_path / "evidence.json",
    )
    state.session_factory = lambda: _session_scope()
    return state


async def _qualify(
    state,
    *,
    provider_profile_ref: str = "opencode-go-default",
    qualified_model: str = "opencode-go/muse-spark-1.2-contributor",
) -> dict:
    controller = controller_module.BootstrapController(
        session_factory=state.session_factory
    )
    return await controller._qualify_and_publish(
        provider_profile_ref=provider_profile_ref,
        qualified_model=qualified_model,
        effort="xhigh",
        resolved=_resolved_state(),
        record=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_qualification_attests_the_launch_policy_admission_selects(
    qualification_boundary,
) -> None:
    """Qualification derives the launch policy from the Agent Profile.

    Restating a harness-shaped ref here attests a support combination no plan
    ever compiles, and every OpenCode launch then fails evidence admission.
    """

    evidence = await _qualify(qualification_boundary)

    expected = default_launch_policy_ref(_ALLOWED_LAUNCH_POLICIES)
    assert expected == "omnigent-on-demand@1"
    assert evidence["supportIdentity"]["launchPolicyRef"] == expected


@pytest.mark.asyncio
async def test_qualification_attests_the_selected_provider_route(
    qualification_boundary,
) -> None:
    """Qualification and admission derive the model route from the same profile."""

    from moonmind.omnigent.harness_platform.execution_plan import (
        compute_model_config_digest,
    )

    qualified_model = "opencode/muse-spark-1.2-contributor-free"
    evidence = await _qualify(
        qualification_boundary,
        provider_profile_ref="opencode-zen-free",
        qualified_model=qualified_model,
    )

    assert evidence["supportIdentity"]["modelConfigDigest"] == (
        compute_model_config_digest(
            qualifiedId=qualified_model,
            effort="xhigh",
            routeRef="opencode",
            normalizedOptions={},
        )
    )


@pytest.mark.asyncio
async def test_recorded_qualification_loader_selects_and_validates_exact_evidence(
    qualification_boundary,
    tmp_path,
) -> None:
    from moonmind.omnigent.deployment_evidence import (
        load_deployment_evidence_for_support_combination,
    )

    evidence = await _qualify(qualification_boundary)
    path = tmp_path / "deployment-evidence.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {"supportCombinationKey": ("omnigent-support:sha256:" + "f" * 64)},
                    evidence,
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = load_deployment_evidence_for_support_combination(
        evidence["supportCombinationKey"],
        path=path,
    )

    assert loaded.support_combination_key == evidence["supportCombinationKey"]


@pytest.mark.asyncio
async def test_qualification_follows_a_reordered_allow_list(
    qualification_boundary,
) -> None:
    """Editing the Agent Profile moves qualification with admission."""

    document = _profile_document()
    document["allowedLaunchPolicyRefs"] = list(reversed(_ALLOWED_LAUNCH_POLICIES))
    qualification_boundary.session.version = _version_row(document, version=28)

    evidence = await _qualify(qualification_boundary)

    assert evidence["supportIdentity"]["launchPolicyRef"] == ("opencode-on-demand@1")


@pytest.mark.asyncio
async def test_qualification_accepts_current_immutable_policy_version(
    qualification_boundary,
) -> None:
    """Image cutover policy versions remain compilable after bootstrap."""

    document = _profile_document()
    document["allowedLaunchPolicyRefs"] = ["omnigent-on-demand@2"]
    qualification_boundary.session.version = _version_row(document, version=29)

    evidence = await _qualify(qualification_boundary)

    assert evidence["supportIdentity"]["launchPolicyRef"] == (
        "omnigent-on-demand@2"
    )


@pytest.mark.asyncio
async def test_qualification_rejects_a_profile_without_a_launch_policy(
    qualification_boundary,
) -> None:
    """An Agent Profile with no allowed launch policy fails before attesting."""

    document = _profile_document()
    document["allowedLaunchPolicyRefs"] = []
    qualification_boundary.session.version = _version_row(document, version=29)

    with pytest.raises(ValueError, match="no allowed launch policy"):
        await _qualify(qualification_boundary)


@pytest.mark.asyncio
async def test_runtime_qualification_rejects_missing_model_catalog_evidence() -> None:
    from moonmind.omnigent.bootstrap.qualification import run_qualification

    profile = SimpleNamespace(
        runtime_id="opencode",
        provider_id="opencode",
        secret_refs={},
        model_catalog_evidence_json=None,
    )

    @asynccontextmanager
    async def _session_scope():
        yield SimpleNamespace(get=AsyncMock(return_value=profile))

    with pytest.raises(RuntimeError, match="not in catalog"):
        await run_qualification(
            session_factory=lambda: _session_scope(),
            provider_profile_ref="opencode-zen-free",
            model_qualified_id="opencode/muse-spark-1.2-contributor-free",
            effort="xhigh",
            host_image_ref=_HOST_IMAGE_REF,
            server_build_digest="sha256:" + "b" * 64,
        )


def test_model_resolution_accepts_a_qualified_opencode_model_before_live_validation() -> (
    None
):
    resolved = resolve_model_by_display("opencode-go/gpt-5.6-luna")

    assert resolved == {
        "displayName": "opencode-go/gpt-5.6-luna",
        "providerModelId": "gpt-5.6-luna",
        "qualifiedId": "opencode-go/gpt-5.6-luna",
    }


@pytest.mark.asyncio
async def test_requalification_uses_the_current_provider_profile_model(
    monkeypatch,
) -> None:
    """Retry must not requalify a stale bootstrap-time model selection."""

    from api_service.db.models import (
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
        SecretStatus,
    )
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        ProviderReconcileOutcome,
    )

    stored = BootstrapRecord(
        state=BootstrapState.failed,
        desired=BootstrapDesired(
            modelDisplayName="Muse Spark 1.2 Contributor",
            effort="xhigh",
            acceptContributorDataUse=True,
        ),
        providerProfileRef="opencode-go-default",
    )
    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        provider_id="opencode-go",
        is_default=True,
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        runtime_id="opencode",
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        cooldown_after_429_seconds=0,
        secret_refs={"opencode_api_key": "db://opencode-go-default-api-key"},
        # #3821: backend-derived isolation policy for
        # opencode/opencode-go/api_key.
        clear_env_keys=[
            "OPENCODE_AUTH_CONTENT",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ],
        command_behavior={},
        default_model="opencode-go/gpt-5.6-luna",
        default_effort="high",
    )

    @asynccontextmanager
    async def _session_scope():
        yield SimpleNamespace(get=AsyncMock(return_value=provider_profile))

    reconciled: list[BootstrapRecord] = []

    async def _reconcile(*, record, api_key, principal):
        assert api_key is None
        assert principal is None
        reconciled.append(record)
        return record

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stored)
    monkeypatch.setattr(
        controller_module, "save_bootstrap_record", lambda _record: None
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(
            return_value={"opencode-go-default-api-key": SecretStatus.ACTIVE.value}
        ),
    )
    revalidate = AsyncMock(return_value=ProviderReconcileOutcome(ready=True))
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.provider_revalidation.reconcile_opencode_provider_readiness",
        revalidate,
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    monkeypatch.setattr(controller, "_reconcile", _reconcile)

    result = await controller.requalify()

    assert reconciled == [result]
    assert result.desired.model_display_name == "opencode-go/gpt-5.6-luna"
    assert result.desired.effort == "high"
    revalidate.assert_awaited_once_with(
        session_factory=controller._session_factory,
        allow_enrollment=False,
        profile_ids=("opencode-go-default",),
    )


@pytest.mark.asyncio
async def test_requalification_follows_the_current_default_provider_profile(
    monkeypatch,
) -> None:
    """Changing the OpenCode default moves qualification to that profile."""

    from api_service.db.models import (
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        ProviderReconcileOutcome,
    )

    stored = BootstrapRecord(
        state=BootstrapState.ready,
        desired=BootstrapDesired(
            modelDisplayName="opencode-go/muse-spark-1.2-contributor",
            effort="xhigh",
            acceptContributorDataUse=True,
        ),
        providerProfileRef="opencode-go-default",
    )
    shared = {
        "enabled": True,
        "auth_state": ProviderProfileAuthState.CONNECTED,
        "disabled_reason": None,
        "max_parallel_runs": 1,
        "runtime_id": "opencode",
        "cooldown_after_429_seconds": 0,
        "command_behavior": {},
    }
    previous = SimpleNamespace(
        **shared,
        profile_id="opencode-go-default",
        provider_id="opencode-go",
        is_default=False,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        secret_refs={"opencode_api_key": "db://opencode-go-default-api-key"},
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
    )
    current = SimpleNamespace(
        **shared,
        profile_id="opencode-zen-free",
        provider_id="opencode",
        is_default=True,
        credential_source=ProviderCredentialSource.NONE,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        secret_refs={},
        default_model="opencode/muse-spark-1.2-contributor-free",
        default_effort="xhigh",
    )

    class _DefaultProfileSession:
        async def get(self, _model, identity):
            return {
                previous.profile_id: previous,
                current.profile_id: current,
            }.get(identity)

        async def scalar(self, _statement):
            return current

    @asynccontextmanager
    async def _session_scope():
        yield _DefaultProfileSession()

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stored)
    monkeypatch.setattr(
        controller_module, "save_bootstrap_record", lambda _record: None
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.provider_revalidation.reconcile_opencode_provider_readiness",
        AsyncMock(return_value=ProviderReconcileOutcome(ready=True)),
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )

    async def _reconcile(*, record, api_key, principal):
        assert api_key is None
        assert principal is None
        return record

    monkeypatch.setattr(controller, "_reconcile", _reconcile)

    result = await controller.requalify()

    assert result.provider_profile_ref == "opencode-zen-free"
    assert (
        result.desired.model_display_name == "opencode/muse-spark-1.2-contributor-free"
    )
    assert result.desired.effort == "xhigh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "secret_status"),
    [
        (False, "active"),
        (True, "disabled"),
        (True, "deleted"),
    ],
)
async def test_requalification_rejects_an_unlaunchable_provider_profile(
    monkeypatch,
    enabled: bool,
    secret_status: str,
) -> None:
    """Retry cannot publish evidence normal profile selection would reject."""

    from api_service.db.models import (
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )

    stored = BootstrapRecord(
        state=BootstrapState.failed,
        desired=BootstrapDesired(
            modelDisplayName="opencode-go/gpt-5.6-luna",
            effort="high",
        ),
        providerProfileRef="opencode-go-default",
    )
    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        provider_id="opencode-go",
        is_default=True,
        enabled=enabled,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        runtime_id="opencode",
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        cooldown_after_429_seconds=0,
        secret_refs={"opencode_api_key": "db://opencode-go-default-api-key"},
        command_behavior={},
        default_model="opencode-go/gpt-5.6-luna",
        default_effort="high",
    )

    @asynccontextmanager
    async def _session_scope():
        yield SimpleNamespace(get=AsyncMock(return_value=provider_profile))

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stored)
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={"opencode-go-default-api-key": secret_status}),
    )
    revalidate = AsyncMock()
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.provider_revalidation.reconcile_opencode_provider_readiness",
        revalidate,
    )

    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    with pytest.raises(ValueError, match="not launch ready"):
        await controller.requalify()

    revalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_requalification_stops_when_runtime_revalidation_fails(
    monkeypatch,
) -> None:
    """Retry cannot publish after the pinned runtime rejects the credential."""

    from api_service.db.models import (
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
        SecretStatus,
    )
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        ProviderReconcileOutcome,
    )

    stored = BootstrapRecord(
        state=BootstrapState.failed,
        desired=BootstrapDesired(
            modelDisplayName="opencode-go/gpt-5.6-luna",
            effort="high",
        ),
        providerProfileRef="opencode-go-default",
    )
    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        provider_id="opencode-go",
        is_default=True,
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        runtime_id="opencode",
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        cooldown_after_429_seconds=0,
        secret_refs={"opencode_api_key": "db://opencode-go-default-api-key"},
        # #3821: backend-derived isolation policy for
        # opencode/opencode-go/api_key.
        clear_env_keys=[
            "OPENCODE_AUTH_CONTENT",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ],
        command_behavior={},
        default_model="opencode-go/gpt-5.6-luna",
        default_effort="high",
    )

    @asynccontextmanager
    async def _session_scope():
        yield SimpleNamespace(get=AsyncMock(return_value=provider_profile))

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stored)
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(
            return_value={
                "opencode-go-default-api-key": SecretStatus.ACTIVE.value,
            }
        ),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.provider_revalidation.reconcile_opencode_provider_readiness",
        AsyncMock(
            return_value=ProviderReconcileOutcome(
                ready=False,
                deferred=("opencode-go-default",),
            )
        ),
    )

    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    reconcile = AsyncMock()
    monkeypatch.setattr(controller, "_reconcile", reconcile)

    with pytest.raises(ValueError, match="could not be revalidated"):
        await controller.requalify()

    reconcile.assert_not_awaited()


def _ready_bootstrap_record(*, agent_profile_ref: str) -> BootstrapRecord:
    return BootstrapRecord(
        state=BootstrapState.ready,
        desired=BootstrapDesired(
            modelDisplayName="opencode-go/muse-spark-1.2-contributor",
            effort="xhigh",
            acceptContributorDataUse=True,
        ),
        resolved=BootstrapResolved(
            qualifiedModelId="opencode-go/muse-spark-1.2-contributor",
            hostImageRef=_HOST_IMAGE_REF,
            omnigentBuildDigest="sha256:" + "a" * 64,
            architecture="linux/amd64",
        ),
        providerProfileRef="opencode-go-default",
        agentProfileRef=agent_profile_ref,
        lastEvidenceRef="omnigent-support:sha256:" + "b" * 64,
    )


@pytest.mark.asyncio
async def test_credentialless_default_initializes_deployment_qualification(
    monkeypatch,
) -> None:
    from api_service.db.models import (
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )

    profile = SimpleNamespace(
        profile_id="opencode-zen-free",
        runtime_id="opencode",
        provider_id="opencode",
        provider_label="OpenCode Zen",
        is_default=True,
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        credential_source=ProviderCredentialSource.NONE,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        secret_refs={},
        command_behavior={
            "auth_readiness": {
                "connected": True,
                "backing_secret_exists": False,
                "launch_ready": True,
            }
        },
        default_model="opencode/muse-spark-1.2-contributor-free",
        default_effort="xhigh",
        max_parallel_runs=1,
        cooldown_after_429_seconds=0,
    )

    class _QualificationSession:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [profile])
            )

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: None)
    saved: list[BootstrapRecord] = []
    monkeypatch.setattr(controller_module, "save_bootstrap_record", saved.append)
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={}),
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )

    async def _reconcile(*, record, api_key, principal):
        assert api_key is None
        assert principal is None
        assert record.provider_profile_ref == "opencode-zen-free"
        assert (
            record.desired.model_display_name
            == "opencode/muse-spark-1.2-contributor-free"
        )
        assert record.desired.effort == "xhigh"
        return record.model_copy(update={"state": BootstrapState.ready})

    monkeypatch.setattr(controller, "_reconcile", _reconcile)
    ensure_materializers = AsyncMock(return_value=True)
    monkeypatch.setattr(
        controller,
        "_ensure_launchable_materializer_qualifications",
        ensure_materializers,
    )

    assert await controller.reconcile_deployment_qualification()
    assert saved[-1].provider_profile_ref == "opencode-zen-free"
    ensure_materializers.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_default_materializer_initializes_when_default_is_not_launch_ready(
    monkeypatch,
) -> None:
    """A broken runtime default must not prevent credentialless Zen evidence."""

    disabled_default = SimpleNamespace(
        profile_id="opencode-go-default",
        runtime_id="opencode",
        provider_id="opencode-go",
        is_default=True,
        enabled=True,
        priority=200,
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
    )
    zen_profile = SimpleNamespace(
        profile_id="opencode-zen-free",
        runtime_id="opencode",
        provider_id="opencode",
        is_default=False,
        enabled=True,
        priority=100,
        default_model=None,
        default_effort=None,
        model_tiers=[
            {
                "label": "Zen free",
                "model": "opencode/muse-spark-1.2-contributor-free",
                "effort": "high",
            }
        ],
        default_model_tier=1,
    )
    profiles = [disabled_default, zen_profile]

    class _QualificationSession:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: profiles)
            )

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: None)
    monkeypatch.setattr(
        controller_module, "save_bootstrap_record", lambda _record: None
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_readiness.provider_profile_launch_ready",
        lambda profile, **_kwargs: profile.profile_id == "opencode-zen-free",
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )

    async def _reconcile(*, record, api_key, principal):
        assert api_key is None
        assert principal is None
        assert record.provider_profile_ref == "opencode-zen-free"
        assert (
            record.desired.model_display_name
            == "opencode/muse-spark-1.2-contributor-free"
        )
        assert record.desired.effort == "high"
        return record.model_copy(update={"state": BootstrapState.ready})

    monkeypatch.setattr(controller, "_reconcile", _reconcile)
    ensure_materializers = AsyncMock(return_value=True)
    monkeypatch.setattr(
        controller,
        "_ensure_launchable_materializer_qualifications",
        ensure_materializers,
    )

    assert await controller.reconcile_deployment_qualification()
    ensure_materializers.assert_awaited_once()


@pytest.mark.asyncio
async def test_managed_agent_profile_advance_requalifies_default_deployment(
    monkeypatch,
) -> None:
    """Catalog-owned profile advancement must not strand default launches."""

    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        is_default=True,
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
        credential_generation=1,
    )
    agent_profile = SimpleNamespace(
        profile_id="omnigent-opencode-default",
        active_version=8,
    )
    active_version = SimpleNamespace(version=8)

    class _QualificationSession:
        async def get(self, model, _identity):
            return {
                "ManagedAgentProviderProfile": provider_profile,
                "OmnigentAgentProfile": agent_profile,
            }.get(model.__name__)

        async def scalar(self, _statement):
            return active_version

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    current_images = ResolvedOmnigentDeploymentState(
        serverImageRef=_SERVER_IMAGE_REF,
        opencodeHostImageRef=_HOST_IMAGE_REF,
        omnigentBuildDigest="sha256:" + "a" * 64,
        architecture="linux/amd64",
    )
    evidence = SimpleNamespace(
        host_image_ref=_HOST_IMAGE_REF,
        provider={"profileRef": "opencode-go-default", "credentialGeneration": 1},
        model={
            "qualifiedId": "opencode-go/muse-spark-1.2-contributor",
            "effort": "xhigh",
        },
    )
    stale = _ready_bootstrap_record(agent_profile_ref="omnigent-opencode-default@7")
    refreshed = stale.model_copy(
        update={"agent_profile_ref": "omnigent-opencode-default@8"}
    )
    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stale)
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.store.load_resolved_state",
        lambda: current_images,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.load_deployment_evidence_for_support_combination",
        lambda _key: evidence,
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    requalify = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(controller, "requalify", requalify)
    monkeypatch.setattr(
        controller,
        "_ensure_launchable_materializer_qualifications",
        AsyncMock(return_value=True),
    )

    assert await controller.reconcile_deployment_qualification()
    requalify.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_secondary_qualification_survives_default_requalification_failure(
    monkeypatch,
) -> None:
    """A drifted default cannot overwrite successful secondary evidence."""

    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        runtime_id="opencode",
        is_default=True,
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
        credential_generation=1,
    )
    agent_profile = SimpleNamespace(
        profile_id="omnigent-opencode-default",
        active_version=8,
    )

    class _QualificationSession:
        async def get(self, model, _identity):
            return {
                "ManagedAgentProviderProfile": provider_profile,
                "OmnigentAgentProfile": agent_profile,
            }.get(model.__name__)

        async def scalar(self, _statement):
            return SimpleNamespace(version=8)

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    current = _ready_bootstrap_record(agent_profile_ref="omnigent-opencode-default@7")
    current_images = ResolvedOmnigentDeploymentState(
        serverImageRef=_SERVER_IMAGE_REF,
        opencodeHostImageRef=_HOST_IMAGE_REF,
        omnigentBuildDigest="sha256:" + "a" * 64,
        architecture="linux/amd64",
    )
    evidence = SimpleNamespace(
        host_image_ref=_HOST_IMAGE_REF,
        provider={"profileRef": "opencode-go-default", "credentialGeneration": 1},
        model={
            "qualifiedId": "opencode-go/muse-spark-1.2-contributor",
            "effort": "xhigh",
        },
    )
    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: current)
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.store.load_resolved_state",
        lambda: current_images,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.load_deployment_evidence_for_support_combination",
        lambda _key: evidence,
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    ensure_materializers = AsyncMock(return_value=True)
    monkeypatch.setattr(
        controller,
        "_ensure_launchable_materializer_qualifications",
        ensure_materializers,
    )
    monkeypatch.setattr(
        controller,
        "requalify",
        AsyncMock(side_effect=ValueError("default profile is not launch ready")),
    )

    assert await controller.reconcile_deployment_qualification()
    ensure_materializers.assert_awaited_once_with(current)


@pytest.mark.asyncio
async def test_current_default_qualification_avoids_repeating_live_workload(
    monkeypatch,
) -> None:
    """The maintenance cadence must not re-run qualification without drift."""

    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default",
        is_default=True,
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
        credential_generation=1,
    )
    agent_profile = SimpleNamespace(
        profile_id="omnigent-opencode-default",
        active_version=8,
    )

    class _QualificationSession:
        async def get(self, model, _identity):
            return {
                "ManagedAgentProviderProfile": provider_profile,
                "OmnigentAgentProfile": agent_profile,
            }.get(model.__name__)

        async def scalar(self, _statement):
            return SimpleNamespace(version=8)

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    current = _ready_bootstrap_record(agent_profile_ref="omnigent-opencode-default@8")
    current_images = ResolvedOmnigentDeploymentState(
        serverImageRef=_SERVER_IMAGE_REF,
        opencodeHostImageRef=_HOST_IMAGE_REF,
        omnigentBuildDigest="sha256:" + "a" * 64,
        architecture="linux/amd64",
    )
    evidence = SimpleNamespace(
        host_image_ref=_HOST_IMAGE_REF,
        provider={"profileRef": "opencode-go-default", "credentialGeneration": 1},
        model={
            "qualifiedId": "opencode-go/muse-spark-1.2-contributor",
            "effort": "xhigh",
        },
    )
    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: current)
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.store.load_resolved_state",
        lambda: current_images,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.load_deployment_evidence_for_support_combination",
        lambda _key: evidence,
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    requalify = AsyncMock()
    monkeypatch.setattr(controller, "requalify", requalify)
    ensure_materializers = AsyncMock(return_value=True)
    monkeypatch.setattr(
        controller,
        "_ensure_launchable_materializer_qualifications",
        ensure_materializers,
    )

    assert await controller.reconcile_deployment_qualification()
    requalify.assert_not_awaited()
    ensure_materializers.assert_awaited_once_with(current)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "zen_already_published",
        "zen_uses_tiers",
        "primary_evidence_available",
        "default_revalidation_ready",
        "expected_qualification_count",
        "expected_revalidation_count",
        "expected_ready",
    ),
    [
        (False, False, True, True, 1, 1, True),
        (True, False, True, True, 0, 0, True),
        (False, True, True, True, 1, 1, True),
        (False, False, False, False, 1, 2, False),
    ],
)
async def test_launchable_materializer_classes_are_qualified_once(
    monkeypatch,
    zen_already_published: bool,
    zen_uses_tiers: bool,
    primary_evidence_available: bool,
    default_revalidation_ready: bool,
    expected_qualification_count: int,
    expected_revalidation_count: int,
    expected_ready: bool,
) -> None:
    """A non-default Zen profile must retain exact ``none@1`` evidence."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        ProviderReconcileOutcome,
    )
    from moonmind.omnigent.harness_platform.support import SupportKeyPayload

    def _support_identity(materializer_ref: str) -> SupportKeyPayload:
        return SupportKeyPayload(
            omnigentServerBuildRef="sha256:" + "a" * 64,
            omnigentHostBuildRef="sha256:" + "a" * 64,
            harnessImplementationRef=(
                "omnigent-harness-implementation:sha256:" + "c" * 64
            ),
            vendorRuntimeRefs=["opencode@1.18.11#sha256:" + "d" * 64],
            agentSourceRef="agent-source:sha256:" + "e" * 64,
            materializerRefs=[materializer_ref],
            providerCompatibilityClass="opencode-native.primary-model",
            hostClassRef="omnigent-opencode@1",
            architecture="linux/amd64",
            launchPolicyRef="omnigent-on-demand@1",
            modelConfigDigest="sha256:" + "f" * 64,
            executionRealizerRef="generic-omnigent-host@1",
            requiredCapabilitiesDigest="sha256:" + "1" * 64,
        )

    def _evidence(
        profile,
        materializer_ref: str,
        *,
        model: str,
        effort: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            support_identity=_support_identity(materializer_ref),
            host_image_ref=_HOST_IMAGE_REF,
            provider={
                "profileRef": profile.profile_id,
                "credentialGeneration": profile.credential_generation,
            },
            model={
                "qualifiedId": model,
                "effort": effort,
            },
        )

    shared = {
        "runtime_id": "opencode",
        "enabled": True,
        "priority": 100,
        "credential_generation": 1,
    }
    go_profile = SimpleNamespace(
        **shared,
        profile_id="opencode-go-default",
        provider_id="opencode-go",
        is_default=True,
        default_model="opencode-go/muse-spark-1.2-contributor",
        default_effort="xhigh",
    )
    zen_profile = SimpleNamespace(
        **shared,
        profile_id="opencode-zen-free",
        provider_id="opencode",
        is_default=False,
        default_model=(
            None if zen_uses_tiers else "opencode/muse-spark-1.2-contributor-free"
        ),
        default_effort=None if zen_uses_tiers else "xhigh",
        model_tiers=(
            [
                {
                    "label": "Zen free",
                    "model": "opencode/muse-spark-1.2-contributor-free",
                    "effort": "high",
                }
            ]
            if zen_uses_tiers
            else []
        ),
        default_model_tier=1,
    )
    profiles = [go_profile, zen_profile]

    class _QualificationSession:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: profiles)
            )

        async def get(self, _model, identity):
            return {profile.profile_id: profile for profile in profiles}.get(identity)

    @asynccontextmanager
    async def _session_scope():
        yield _QualificationSession()

    current_images = ResolvedOmnigentDeploymentState(
        serverImageRef=_SERVER_IMAGE_REF,
        opencodeHostImageRef=_HOST_IMAGE_REF,
        omnigentBuildDigest="sha256:" + "a" * 64,
        architecture="linux/amd64",
    )
    go_evidence = _evidence(
        go_profile,
        "opencode-auth-json@1",
        model="opencode-go/muse-spark-1.2-contributor",
        effort="xhigh",
    )
    zen_evidence = _evidence(
        zen_profile,
        "none@1",
        model="opencode/muse-spark-1.2-contributor-free",
        effort="high" if zen_uses_tiers else "xhigh",
    )
    published = [go_evidence]
    if zen_already_published:
        published.append(zen_evidence)

    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.store.load_resolved_state",
        lambda: current_images,
    )

    def _load_primary(_key):
        if not primary_evidence_available:
            raise ValueError("primary evidence unavailable")
        return go_evidence

    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.load_deployment_evidence_for_support_combination",
        _load_primary,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.load_deployment_evidence_entries",
        lambda: tuple(published),
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_readiness.provider_profile_launch_ready",
        lambda *_args, **_kwargs: True,
    )

    async def _revalidate(*, profile_ids, **_kwargs):
        profile_ref = profile_ids[0]
        return ProviderReconcileOutcome(
            ready=(
                default_revalidation_ready
                if profile_ref == "opencode-go-default"
                else True
            )
        )

    revalidate = AsyncMock(side_effect=_revalidate)
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.provider_revalidation.reconcile_opencode_provider_readiness",
        revalidate,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_evidence.validate_deployment_evidence",
        lambda _payload: zen_evidence,
    )
    controller = controller_module.BootstrapController(
        session_factory=lambda: _session_scope()
    )
    qualify = AsyncMock(return_value={})
    monkeypatch.setattr(controller, "_qualify_and_publish", qualify)

    record = _ready_bootstrap_record(agent_profile_ref="omnigent-opencode-default@8")
    assert (
        await controller._ensure_launchable_materializer_qualifications(record)
        is expected_ready
    )
    assert qualify.await_count == expected_qualification_count
    assert revalidate.await_count == expected_revalidation_count
    if expected_qualification_count:
        assert qualify.await_args.kwargs["provider_profile_ref"] == (
            "opencode-zen-free"
        )
        assert qualify.await_args.kwargs["qualified_model"] == (
            "opencode/muse-spark-1.2-contributor-free"
        )
        assert qualify.await_args.kwargs["effort"] == (
            "high" if zen_uses_tiers else "xhigh"
        )
