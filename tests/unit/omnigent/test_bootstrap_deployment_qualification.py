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
    digest = "sha256:" + hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
        return SimpleNamespace(credential_generation=1)

    async def execute(self, _statement):
        version = self.version
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: version)
        )


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

    monkeypatch.setattr(
        qualification_module, "run_qualification", _run_qualification
    )
    monkeypatch.setattr(
        evidence_module,
        "write_deployment_evidence",
        lambda evidence, path=None: tmp_path / "evidence.json",
    )
    state.session_factory = lambda: _session_scope()
    return state


async def _qualify(state) -> dict:
    controller = controller_module.BootstrapController(
        session_factory=state.session_factory
    )
    return await controller._qualify_and_publish(
        provider_profile_ref="opencode-go-default",
        qualified_model="opencode-go/muse-spark-1.2-contributor",
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
                    {
                        "supportCombinationKey": (
                            "omnigent-support:sha256:" + "f" * 64
                        )
                    },
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
    document["allowedLaunchPolicyRefs"] = list(
        reversed(_ALLOWED_LAUNCH_POLICIES)
    )
    qualification_boundary.session.version = _version_row(document, version=28)

    evidence = await _qualify(qualification_boundary)

    assert evidence["supportIdentity"]["launchPolicyRef"] == (
        "opencode-on-demand@1"
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


def test_model_resolution_accepts_a_qualified_opencode_model_before_live_validation() -> None:
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
        enabled=True,
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

    reconciled: list[BootstrapRecord] = []

    async def _reconcile(*, record, api_key, principal):
        assert api_key is None
        assert principal is None
        reconciled.append(record)
        return record

    monkeypatch.setattr(controller_module, "load_bootstrap_record", lambda: stored)
    monkeypatch.setattr(controller_module, "save_bootstrap_record", lambda _record: None)
    monkeypatch.setattr(
        "api_service.services.provider_profile_service._managed_secret_statuses_for_profiles",
        AsyncMock(return_value={"opencode-go-default-api-key": SecretStatus.ACTIVE.value}),
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
    )


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
        enabled=True,
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
async def test_managed_agent_profile_advance_requalifies_default_deployment(
    monkeypatch,
) -> None:
    """Catalog-owned profile advancement must not strand default launches."""

    provider_profile = SimpleNamespace(
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
    stale = _ready_bootstrap_record(
        agent_profile_ref="omnigent-opencode-default@7"
    )
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

    assert await controller.reconcile_deployment_qualification()
    requalify.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_current_default_qualification_avoids_repeating_live_workload(
    monkeypatch,
) -> None:
    """The maintenance cadence must not re-run qualification without drift."""

    provider_profile = SimpleNamespace(
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

    current = _ready_bootstrap_record(
        agent_profile_ref="omnigent-opencode-default@8"
    )
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

    assert await controller.reconcile_deployment_qualification()
    requalify.assert_not_awaited()
