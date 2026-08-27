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
from moonmind.omnigent.bootstrap.controller import BootstrapController
from moonmind.omnigent.bootstrap.models import (
    BootstrapDesired,
    BootstrapRecord,
    BootstrapState,
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
    controller = BootstrapController(session_factory=state.session_factory)
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

    import moonmind.omnigent.bootstrap.controller as controller_module

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
    controller = BootstrapController(session_factory=lambda: _session_scope())
    monkeypatch.setattr(controller, "_reconcile", _reconcile)

    result = await controller.requalify()

    assert reconciled == [result]
    assert result.desired.model_display_name == "opencode-go/gpt-5.6-luna"
    assert result.desired.effort == "high"
