"""MoonLadderStudios/MoonMind#3451 catalog boundary coverage."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_service.api.routers import omnigent_catalog as catalog
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.security.egress import (
    OMNIGENT_EGRESS_NETWORK_REF,
    OMNIGENT_EGRESS_PROFILE,
)

_DEFAULT = object()


def _attested_bridge_session(
    *,
    server_digest: str = "1",
    host_digest: str = "2",
    observed_at: datetime | None = None,
):
    server_sha = server_digest * 64
    host_sha = host_digest * 64
    return SimpleNamespace(
        metadata_={
            catalog.EGRESS_CLEANUP_AUTHORITY_KEY: {
                "schemaVersion": catalog.EGRESS_CLEANUP_AUTHORITY_VERSION,
                "phase": "attested",
                "egressEvidence": {
                    "serverImageRefObserved": (
                        "registry.test/server@sha256:" + server_sha
                    ),
                    "serverImageDigest": "sha256:" + server_sha,
                    "workloadImageRef": "registry.test/host@sha256:" + host_sha,
                    "workloadImageDigest": "sha256:" + host_sha,
                    "validatedAt": (observed_at or datetime.now(UTC)).isoformat(),
                },
            }
        }
    )


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def all(self):
        return self._rows

    def one(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows


class _Session:
    def __init__(
        self,
        profiles,
        *,
        slots=(),
        bindings=(),
        host_leases=(),
        policies=(),
        bridge_sessions=_DEFAULT,
        schema_versions=(1, 1),
        latest_observation_at=_DEFAULT,
        snapshot_observed_at=_DEFAULT,
        event_observed_at=_DEFAULT,
    ):
        if latest_observation_at is _DEFAULT:
            latest_observation_at = datetime.now(UTC)
        if snapshot_observed_at is _DEFAULT:
            snapshot_observed_at = latest_observation_at
        if event_observed_at is _DEFAULT:
            event_observed_at = latest_observation_at
        if bridge_sessions is _DEFAULT:
            bridge_sessions = (_attested_bridge_session(),)
        self._results = iter(
            (
                _Result(profiles),
                _Result(slots),
                _Result(bindings),
                _Result(host_leases),
                _Result(policies),
                _Result(bridge_sessions),
                _Result(schema_versions),
                _Result((snapshot_observed_at, event_observed_at)),
            )
        )

    async def execute(self, _statement):
        return next(self._results)


class _HealthResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise catalog.httpx.HTTPStatusError(
                "unavailable", request=None, response=None
            )

    def json(self):
        return self._payload


def _profile(**overrides):
    values = {
        "profile_id": "codex-oauth",
        "account_label": "OpenAI subscription",
        "provider_label": "OpenAI",
        "provider_id": "openai",
        "runtime_id": "codex_cli",
        "credential_source": SimpleNamespace(value="oauth_volume"),
        "runtime_materialization_mode": SimpleNamespace(value="oauth_home"),
        "rate_limit_policy": SimpleNamespace(value="queue"),
        "max_parallel_runs": 1,
        "owner_user_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _bootstrap_header(
    *,
    server_digest: str = "1",
    host_digest: str = "2",
    observed_at: datetime | None = None,
) -> str:
    return catalog.json.dumps(
        {
            "schemaVersion": catalog._BOOTSTRAP_EVIDENCE_SCHEMA_VERSION,
            "observedAt": (observed_at or datetime.now(UTC)).isoformat(),
            "providerSnapshotObserved": True,
            "eventTransportObserved": True,
            "serverImageRefObserved": (
                "registry.test/server@sha256:" + server_digest * 64
            ),
            "hostImageRefObserved": ("registry.test/host@sha256:" + host_digest * 64),
            "uiBuildRefObserved": "abc123",
        }
    )


def _config(*, enabled=True):
    return SimpleNamespace(
        enabled=enabled,
        host_protocol_mode="upstream_omnigent_server_proxy",
        compatibility=SimpleNamespace(profile="omnigent.server.v1"),
        readiness=lambda **_kwargs: {
            "conformanceState": "ready" if enabled else "disabled",
            "protocolProfile": "omnigent.server.v1",
        },
    )


def _app(monkeypatch, *, session, enabled=True, readiness=None, superuser=True):
    monkeypatch.setattr(catalog, "get_bridge_config", lambda: _config(enabled=enabled))
    monkeypatch.setattr(catalog, "_reconciler_generation_available", lambda: True)
    monkeypatch.setattr(catalog, "_websocket_runtime_available", lambda: True)
    monkeypatch.setattr(
        catalog,
        "_secret_ref_results_for_rows",
        lambda rows: {r.profile_id: {} for r in rows},
    )

    async def statuses(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(catalog, "_managed_secret_statuses_for_rows", statuses)
    monkeypatch.setattr(
        catalog,
        "_provider_profile_readiness",
        lambda *_args, **_kwargs: readiness or {"launch_ready": True, "checks": []},
    )
    monkeypatch.setattr(
        catalog,
        "resolve_container_backend_settings",
        lambda: SimpleNamespace(enabled=True),
    )

    async def live_readiness():
        return catalog.LiveDeploymentReadiness(
            endpoint_ready=True,
            backend_ready=True,
            enforced_network_refs=frozenset({OMNIGENT_EGRESS_NETWORK_REF}),
            enforced_egress_profile_refs=frozenset({OMNIGENT_EGRESS_PROFILE.ref}),
            workflow_types=frozenset({"MoonMind.AgentSession"}),
            activity_types=frozenset(
                {
                    "agent_runtime.reconcile_managed_sessions",
                    "integration.omnigent.oauth_host_janitor",
                }
            ),
            immutable_worker_build=True,
        )

    monkeypatch.setattr(catalog, "_live_deployment_readiness", live_readiness)
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "registry.test/server@sha256:" + "1" * 64)
    monkeypatch.setenv(
        "OMNIGENT_HOST_IMAGE_REF", "registry.test/host@sha256:" + "2" * 64
    )
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent:8000")
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", "/evidence/matrix.json")
    monkeypatch.setenv("MOONMIND_SOURCE_COMMIT", "abc123")
    acceptance_generated_at = datetime.now(UTC).isoformat()
    monkeypatch.setattr(
        catalog.Path,
        "read_text",
        lambda *_args, **_kwargs: catalog.json.dumps(
            {
                "generatedAt": acceptance_generated_at,
                "sourceCommit": "abc123",
                "images": {
                    "serverDigest": "sha256:" + "1" * 64,
                    "hostDigest": "sha256:" + "2" * 64,
                },
            }
        ),
    )
    monkeypatch.setattr(
        catalog, "validate_acceptance_manifest", lambda *_args, **_kwargs: None
    )
    app = FastAPI()
    app.include_router(catalog.router)
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=None, is_superuser=superuser
    )
    app.dependency_overrides[get_async_session] = lambda: session
    return app


@pytest.mark.asyncio
async def test_generic_readiness_requires_both_feature_gates_and_real_launch_data(
    monkeypatch,
):
    from moonmind.omnigent.harness_platform import catalog_service
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )

    implementation = {
        "sourceKind": "core",
        "package": "omnigent",
        "version": "0.11.0",
        "digest": "sha256:" + "3" * 64,
    }
    snapshot = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest="sha256:" + "4" * 64,
        sourceDigest="sha256:" + "5" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": implementation,
                "capabilities": {"integrationMode": "native-server"},
            }
        ],
    )
    harness = snapshot.harnesses[0]
    trust = classify_harness_trust(
        harnessId=harness.id,
        implementation=harness.implementation,
        trustState=TrustState.core_trusted,
    )
    catalog_result = HarnessCatalogSyncResult(snapshot, (trust,), {})

    class Repository:
        def __init__(self, _session_factory):
            pass

        async def load(self, catalog_ref):
            assert catalog_ref == snapshot.catalogRef
            return catalog_result

        async def latest(self, endpoint_ref):
            assert endpoint_ref == "default"
            return catalog_result

    monkeypatch.setattr(catalog_service, "DbHarnessCatalogRepository", Repository)
    monkeypatch.setattr(
        catalog, "_require_provider_profile_permission", lambda *_: None
    )
    monkeypatch.setattr(catalog, "_can_view_profile", lambda *_: True)
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "registry.test/opencode@sha256:" + "6" * 64,
    )

    document = {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": "default",
        "source": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1",
            "upstreamSnapshotDigest": "sha256:" + "7" * 64,
        },
        "harness": {
            "id": harness.id,
            "catalogRef": snapshot.catalogRef,
            "implementationRef": harness.implementation.implementation_ref(),
        },
        "credentialSlots": [
            {"id": "primary-model", "acceptedProviderIds": ["opencode-go"]}
        ],
        "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
    }
    profile_row = SimpleNamespace(
        profile_id="omnigent-opencode-default",
        active_version=1,
        visibility="public",
        owner_id=None,
    )
    version = SimpleNamespace(
        version=1,
        digest="sha256:" + "8" * 64,
        document=document,
        validation_result={"ready": True},
    )
    provider = SimpleNamespace(
        profile_id="opencode-go-primary",
        account_label="OpenCode Go",
        provider_id="opencode-go",
        runtime_id="opencode",
        enabled=True,
        auth_state=SimpleNamespace(value="connected"),
        credential_generation=4,
        model_catalog_evidence_json={
            "credentialGeneration": 4,
            "imageRef": "registry.test/opencode@sha256:" + "6" * 64,
            "models": ["opencode-go/test-model"],
        },
    )

    class Session:
        def __init__(self):
            self._results = iter((_Result([profile_row]), _Result([provider])))

        async def execute(self, _statement):
            return next(self._results)

        async def scalar(self, _statement):
            return version

    current_user = SimpleNamespace(id=None, is_superuser=True)
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", "false")
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "false")
    disabled = await catalog.get_omnigent_execution_readiness(
        session=Session(), current_user=current_user
    )
    disabled_target = disabled.execution_targets[0]
    assert disabled_target.available is False
    assert {reason.code for reason in disabled_target.gate_reasons} >= {
        "generic_realizer_not_ready",
        "opencode_support_not_qualified",
    }

    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", "true")
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "true")
    enabled = await catalog.get_omnigent_execution_readiness(
        session=Session(), current_user=current_user
    )
    enabled_target = enabled.execution_targets[0]
    assert enabled_target.available is True
    assert enabled_target.compatible_host_classes == ["omnigent-opencode@1"]
    assert enabled_target.models == ["opencode-go/test-model"]


def test_ready_catalog_lists_only_launch_ready_codex_oauth_profiles(monkeypatch):
    profiles = [
        _profile(),
        _profile(
            profile_id="api-key", credential_source=SimpleNamespace(value="secret_ref")
        ),
    ]
    client = TestClient(_app(monkeypatch, session=_Session(profiles)))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    assert response.status_code == 200
    assert response.json()["available"] is True


def test_reconciler_readiness_uses_the_actual_static_workflow_registration():
    assert catalog._reconciler_generation_available() is True


def test_generic_readiness_hides_other_users_private_agent_profiles() -> None:
    current_user = SimpleNamespace(id="user-a", is_superuser=False)

    assert catalog._can_view_agent_profile(
        SimpleNamespace(visibility="private", owner_id="user-a"), current_user
    )
    assert not catalog._can_view_agent_profile(
        SimpleNamespace(visibility="private", owner_id="user-b"), current_user
    )
    assert catalog._can_view_agent_profile(
        SimpleNamespace(visibility="workspace", owner_id="user-b"), current_user
    )


def test_protected_first_run_canary_uses_normal_catalog_without_published_manifest(
    monkeypatch,
):
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", "canary-secret")
    client = TestClient(
        _app(
            monkeypatch,
            session=_Session(
                [_profile()],
                bridge_sessions=(),
                latest_observation_at=None,
            ),
        )
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", raising=False)
    monkeypatch.delenv("MOONMIND_SOURCE_COMMIT", raising=False)

    response = client.get(
        "/api/omnigent/codex-catalog-readiness",
        headers={
            "X-MoonMind-Acceptance-Canary": "canary-secret",
            catalog._BOOTSTRAP_EVIDENCE_HEADER: _bootstrap_header(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "acceptance_evidence_unavailable" not in {
        reason["code"] for reason in payload["gateReasons"]
    }
    assert payload["available"] is True
    assert payload["admissionReadiness"]["admitNew"] is True


def test_first_run_canary_rejects_an_untrusted_header(monkeypatch):
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", "canary-secret")
    client = TestClient(
        _app(
            monkeypatch,
            session=_Session(
                [_profile()],
                bridge_sessions=(),
                latest_observation_at=None,
            ),
        )
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", raising=False)
    monkeypatch.delenv("MOONMIND_SOURCE_COMMIT", raising=False)

    response = client.get(
        "/api/omnigent/codex-catalog-readiness",
        headers={
            "X-MoonMind-Acceptance-Canary": "wrong",
            catalog._BOOTSTRAP_EVIDENCE_HEADER: _bootstrap_header(),
        },
    )

    body = response.json()
    assert "acceptance_evidence_unavailable" in {
        reason["code"] for reason in body["supportGateReasons"]
    }
    assert body["schemaVersion"] == "moonmind.omnigent-codex-readiness.v2"
    assert body["available"] is False
    assert set(body["admissionReadiness"]["blocking"]) >= {
        "provider_snapshot",
        "event_transport",
        "server_build",
        "ui_build",
        "host_build",
        "exact_image",
        "protected_live_evidence",
    }
    assert body["cutover"] == {
        "policyVersion": "moonmind.codex-omnigent-cutover/v1",
        "configuredPhase": "opt_in",
        "deployedPhase": "opt_in",
        "phase": "opt_in",
        "promotionAllowed": True,
        "evidenceRef": None,
        "evidenceSha256": None,
        "generatedAt": None,
        "expiresAt": None,
        "profileVersion": None,
        "profileSha256": None,
        "launchPolicyVersion": None,
        "agentProfileVersion": None,
        "matrixVersion": None,
        "matrixRows": [],
        "images": {},
        "architectures": [],
        "thresholds": {},
        "evidenceRefs": [],
        "blockers": [],
        "directLaunchAllowed": True,
    }
    # Operator-remediation release status (MoonLadderStudios/MoonMind#3626) is
    # published on the same readiness endpoint and is fail-closed by default:
    # with no mounted evidence document nothing is supported and the autonomous
    # rollout gate stays closed.
    remediation = body["remediationRelease"]
    assert remediation["matrixVersion"] == "operator-remediation-support-matrix/v1"
    assert remediation["manualDiagnosisSupported"] is False
    assert remediation["manualMutationSupported"] is False
    assert remediation["autonomousRolloutAuthorized"] is False
    assert remediation["promotionAllowed"] is False
    assert "autonomous_rollout_gate_closed" in remediation["blockers"]
    assert "remediation_release_evidence_missing" in remediation["blockers"]
    assert body["hostModes"] == ["on_demand_docker"]
    assert body["eligibleProviderProfiles"] == [
        {
            "profileId": "codex-oauth",
            "label": "OpenAI subscription",
            "providerId": "openai",
            "runtimeId": "codex_cli",
            "busy": False,
            "queueWhenBusy": True,
        }
    ]
    assert body["ineligibleProviderProfiles"] == []
    diagnostics = body["compatibilityDiagnostics"]
    assert diagnostics["bridgeMode"] == "upstream_omnigent_server_proxy"
    assert diagnostics["compatibilityProfile"] == "omnigent.server.v1"
    assert diagnostics["evidence"]["fresh"] is True
    assert diagnostics["failureReason"] is None
    assert diagnostics["rollbackRecommendation"] is None
    assert diagnostics["capabilitySummary"] == []
    assert diagnostics["releaseMetadata"]["bridgeMode"] == (
        "upstream_omnigent_server_proxy"
    )
    assert {row["hostMode"] for row in diagnostics["supportMatrix"]} == {
        "static_compose",
        "on_demand_docker",
    }


def test_catalog_summarizes_persisted_stock_host_harnesses(monkeypatch):
    lease = SimpleNamespace(
        provider_profile_id="codex-oauth",
        host_capabilities_json={"harnesses": ["codex-native"]},
    )
    client = TestClient(
        _app(monkeypatch, session=_Session([_profile()], host_leases=[lease]))
    )

    response = client.get("/api/omnigent/codex-catalog-readiness")

    assert response.status_code == 200
    assert response.json()["compatibilityDiagnostics"]["capabilitySummary"] == [
        "codex-native"
    ]


def test_catalog_projects_runtime_identity_for_mixed_provider_profiles(monkeypatch):
    profiles = [
        _profile(),
        _profile(
            profile_id="claude-oauth",
            account_label="Anthropic subscription",
            provider_label="Anthropic",
            provider_id="anthropic",
            runtime_id="claude_code",
        ),
    ]

    body = (
        TestClient(_app(monkeypatch, session=_Session(profiles)))
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    assert {
        item["profileId"]: item["runtimeId"]
        for item in body["eligibleProviderProfiles"]
    } == {
        "codex-oauth": "codex_cli",
        "claude-oauth": "claude_code",
    }


def test_catalog_returns_actionable_bounded_redacted_gates(monkeypatch):
    secret = "github_pat_SHOULD_NOT_ESCAPE"
    profile = _profile(account_label=secret)
    client = TestClient(_app(monkeypatch, session=_Session([profile]), enabled=False))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    body = response.json()
    assert body["available"] is False
    assert {reason["code"] for reason in body["gateReasons"]} >= {"bridge_disabled"}
    assert all(
        reason["message"] and reason["remediationHref"]
        for reason in body["gateReasons"]
    )
    assert secret not in response.text
    for forbidden in (
        "volume",
        "hostId",
        "docker.sock",
        "token=",
        "header",
        "environment",
    ):
        assert forbidden not in response.text


def test_catalog_requires_authentication():
    app = FastAPI()
    app.include_router(catalog.router)

    def unauthenticated():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user()] = unauthenticated
    response = TestClient(app).get("/api/omnigent/codex-catalog-readiness")
    assert response.status_code in {401, 403}


def test_catalog_reports_mixed_profile_reconnect_and_capacity(monkeypatch):
    reconnect = _profile(profile_id="reconnect")
    busy = _profile(
        profile_id="busy", rate_limit_policy=SimpleNamespace(value="reject")
    )
    slot = SimpleNamespace(
        profile_id="busy", expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )

    def profile_readiness(row, **_kwargs):
        if row.profile_id == "reconnect":
            return {
                "launch_ready": False,
                "checks": [{"id": "auth_state", "status": "error"}],
            }
        return {"launch_ready": True, "checks": []}

    app = _app(
        monkeypatch,
        session=_Session([reconnect, busy], slots=[slot]),
    )
    monkeypatch.setattr(catalog, "_provider_profile_readiness", profile_readiness)

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["eligibleProviderProfiles"] == []
    assert {
        item["profileId"]: {reason["code"] for reason in item["gateReasons"]}
        for item in body["ineligibleProviderProfiles"]
    } == {
        "reconnect": {"profile_reconnect_required"},
        "busy": {"profile_capacity_unavailable"},
    }


def test_busy_profile_is_eligible_when_queueing_is_permitted(monkeypatch):
    profile = _profile(profile_id="busy")
    slot = SimpleNamespace(
        profile_id="busy", expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    body = (
        TestClient(_app(monkeypatch, session=_Session([profile], slots=[slot])))
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    assert body["eligibleProviderProfiles"][0]["busy"] is True
    assert body["eligibleProviderProfiles"][0]["queueWhenBusy"] is True


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"OMNIGENT_ENABLED": "false"}, "rollout_gate_disabled"),
        ({"OMNIGENT_SERVER_URL": ""}, "bridge_endpoint_unavailable"),
        ({"OMNIGENT_SERVER_URL": "omnigent:8000"}, "bridge_endpoint_unavailable"),
        (
            {"MOONMIND_WORKSPACE_RESOLVER_ENABLED": "false"},
            "workspace_resolver_unavailable",
        ),
        ({"OMNIGENT_IMAGE_REF": "mutable:latest"}, "immutable_image_unavailable"),
    ],
)
def test_catalog_projects_authoritative_deployment_gates(
    monkeypatch, environment, expected
):
    app = _app(monkeypatch, session=_Session([_profile()]))
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is False
    assert expected in {reason["code"] for reason in body["gateReasons"]}


@pytest.mark.parametrize(
    ("manifest_path", "source_commit"),
    [
        ("", "abc123"),
        ("/missing/matrix.json", "abc123"),
        ("/evidence/matrix.json", ""),
    ],
)
def test_catalog_fails_closed_when_protected_acceptance_evidence_is_missing(
    monkeypatch, manifest_path, source_commit
):
    app = _app(monkeypatch, session=_Session([_profile()]))
    monkeypatch.setattr(
        catalog,
        "validate_acceptance_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            catalog.ConformanceContractError("invalid")
        ),
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", manifest_path)
    monkeypatch.setenv("MOONMIND_SOURCE_COMMIT", source_commit)

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is False
    assert "acceptance_evidence_unavailable" in {
        reason["code"] for reason in body["supportGateReasons"]
    }
    assert "protected_live_evidence" in body["admissionReadiness"]["blocking"]
    assert "omnigent_admission_readiness_failed" in {
        reason["code"] for reason in body["gateReasons"]
    }


@pytest.mark.parametrize(
    ("capability", "helper"),
    [
        ("reconciler_generation", "_reconciler_generation_available"),
        ("websocket", "_websocket_runtime_available"),
    ],
)
def test_catalog_fails_closed_on_missing_loaded_runtime_capability(
    monkeypatch, capability, helper
):
    app = _app(monkeypatch, session=_Session([_profile()]))
    monkeypatch.setattr(catalog, helper, lambda: False)

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is False
    assert capability in body["admissionReadiness"]["blocking"]
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_catalog_rejects_placeholder_image_digests(monkeypatch):
    app = _app(monkeypatch, session=_Session([_profile()]))
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "registry.test/server@sha256:" + "0" * 64)

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is False
    assert "immutable_image_unavailable" in {
        reason["code"] for reason in body["gateReasons"]
    }


def test_resolved_persisted_policy_keeps_latest_from_being_a_launch_blocker(
    monkeypatch,
):
    identity = SimpleNamespace(
        policy_id="codex-on-demand",
        name="On-demand Docker",
        default_version=2,
        visibility="deployment",
        owner_user_id=None,
    )
    version = SimpleNamespace(
        version=2,
        state="active",
        validation_json={"valid": True},
        document_json={
            "execution": {"profileRef": "omnigent-codex@1"},
            "host": {
                "mode": "on_demand_docker",
                "serverImageRef": "registry.test/server@sha256:" + "1" * 64,
                "hostImageRef": "registry.test/host@sha256:" + "2" * 64,
            },
            "network": {
                "attachmentRef": OMNIGENT_EGRESS_NETWORK_REF,
                "egressProfileRef": OMNIGENT_EGRESS_PROFILE.ref,
            },
        },
    )
    app = _app(
        monkeypatch,
        session=_Session([_profile()], policies=[(identity, version)]),
    )
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "")
    monkeypatch.setenv("OMNIGENT_IMAGE", "registry.test/server")
    monkeypatch.setenv("OMNIGENT_IMAGE_TAG", "latest")
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", "")
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", "registry.test/host")
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_TAG", "latest")
    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is True
    codex_profile = next(
        profile
        for profile in body["executionProfiles"]
        if profile["ref"] == "omnigent-codex@1"
    )
    assert codex_profile["launchPolicies"] == [
        {
            "ref": "codex-on-demand@2",
            "displayName": "On-demand Docker",
            "hostMode": "on_demand_docker",
            "isDefault": True,
        }
    ]
    assert "immutable_image_unavailable" not in {
        reason["code"] for reason in body["gateReasons"]
    }


def test_catalog_retains_all_ready_active_policy_versions(monkeypatch):
    identity = SimpleNamespace(
        policy_id="codex-on-demand",
        name="On-demand Docker",
        default_version=2,
        visibility="deployment",
        owner_user_id=None,
    )

    def version(number):
        return SimpleNamespace(
            version=number,
            state="active",
            validation_json={"valid": True},
            document_json={
                "execution": {"profileRef": "omnigent-codex@1"},
                "host": {
                    "mode": "on_demand_docker",
                    "serverImageRef": "registry.test/server@sha256:" + "1" * 64,
                    "hostImageRef": "registry.test/host@sha256:" + "2" * 64,
                },
                "network": {
                    "attachmentRef": OMNIGENT_EGRESS_NETWORK_REF,
                    "egressProfileRef": OMNIGENT_EGRESS_PROFILE.ref,
                },
            },
        )

    body = (
        TestClient(
            _app(
                monkeypatch,
                session=_Session(
                    [_profile()],
                    policies=[(identity, version(1)), (identity, version(2))],
                ),
            )
        )
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    codex_profile = next(
        profile
        for profile in body["executionProfiles"]
        if profile["ref"] == "omnigent-codex@1"
    )
    assert [
        (policy["ref"], policy["isDefault"])
        for policy in codex_profile["launchPolicies"]
    ] == [
        ("codex-on-demand@2", True),
        ("codex-on-demand@1", False),
    ]


def test_catalog_filters_persisted_policies_not_visible_to_caller(monkeypatch):
    hidden_name = "Private policy name must not escape"
    identity = SimpleNamespace(
        policy_id="codex-private",
        name=hidden_name,
        default_version=1,
        visibility="private",
        owner_user_id="other-user",
    )
    version = SimpleNamespace(
        version=1,
        state="active",
        validation_json={"valid": True},
        document_json={
            "execution": {"profileRef": "omnigent-codex@1"},
            "host": {
                "mode": "on_demand_docker",
                "serverImageRef": "registry.test/server@sha256:" + "1" * 64,
                "hostImageRef": "registry.test/host@sha256:" + "2" * 64,
            },
            "network": {
                "attachmentRef": OMNIGENT_EGRESS_NETWORK_REF,
                "egressProfileRef": OMNIGENT_EGRESS_PROFILE.ref,
            },
        },
    )
    app = _app(
        monkeypatch,
        session=_Session([_profile()], policies=[(identity, version)]),
    )
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id="current-user", is_superuser=False
    )
    monkeypatch.setattr(
        catalog, "_require_provider_profile_permission", lambda *_: None
    )

    response = TestClient(app).get("/api/omnigent/codex-catalog-readiness")

    assert response.status_code == 200
    assert "codex-private@1" not in response.text
    assert hidden_name not in response.text


def test_catalog_filters_profiles_not_visible_to_caller(monkeypatch):
    visible = _profile(profile_id="visible", owner_user_id=None)
    hidden = _profile(profile_id="hidden", owner_user_id="other-user")
    app = _app(monkeypatch, session=_Session([visible, hidden]))
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id="current-user", is_superuser=False
    )
    monkeypatch.setattr(
        catalog, "_require_provider_profile_permission", lambda *_: None
    )

    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert [item["profileId"] for item in body["eligibleProviderProfiles"]] == [
        "visible"
    ]


@pytest.mark.parametrize(
    ("live_readiness", "expected"),
    [
        (
            catalog.LiveDeploymentReadiness(
                backend_ready=True,
                enforced_network_refs=frozenset({OMNIGENT_EGRESS_NETWORK_REF}),
                enforced_egress_profile_refs=frozenset({OMNIGENT_EGRESS_PROFILE.ref}),
            ),
            "bridge_endpoint_not_ready",
        ),
        (
            catalog.LiveDeploymentReadiness(endpoint_ready=True, backend_ready=True),
            "network_policy_unavailable",
        ),
    ],
)
def test_catalog_fails_closed_on_live_service_readiness(
    monkeypatch, live_readiness, expected
):
    app = _app(monkeypatch, session=_Session([_profile()]))

    async def readiness():
        return live_readiness

    monkeypatch.setattr(catalog, "_live_deployment_readiness", readiness)
    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["available"] is False
    assert expected in {reason["code"] for reason in body["gateReasons"]}


def test_catalog_blocks_new_admission_on_stale_persisted_observation(monkeypatch):
    session = _Session(
        [_profile()],
        latest_observation_at=datetime.now(UTC) - timedelta(minutes=11),
    )
    body = (
        TestClient(_app(monkeypatch, session=session))
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )
    assert "observation_freshness" in body["admissionReadiness"]["blocking"]
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_catalog_blocks_new_admission_when_janitor_activity_is_not_deployed(
    monkeypatch,
):
    app = _app(monkeypatch, session=_Session([_profile()]))

    async def readiness():
        return catalog.LiveDeploymentReadiness(
            endpoint_ready=True,
            backend_ready=True,
            enforced_network_refs=frozenset({OMNIGENT_EGRESS_NETWORK_REF}),
            enforced_egress_profile_refs=frozenset({OMNIGENT_EGRESS_PROFILE.ref}),
            workflow_types=frozenset({"MoonMind.AgentSession"}),
            activity_types=frozenset({"agent_runtime.reconcile_managed_sessions"}),
            immutable_worker_build=True,
        )

    monkeypatch.setattr(catalog, "_live_deployment_readiness", readiness)
    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()
    assert "janitor" in body["admissionReadiness"]["blocking"]
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_catalog_blocks_new_admission_on_stale_build_manifest(monkeypatch):
    app = _app(monkeypatch, session=_Session([_profile()]))
    monkeypatch.setattr(
        catalog.Path,
        "read_text",
        lambda *_args, **_kwargs: catalog.json.dumps(
            {
                "generatedAt": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
                "sourceCommit": "abc123",
                "images": {
                    "serverDigest": "sha256:" + "9" * 64,
                    "hostDigest": "sha256:" + "8" * 64,
                },
            }
        ),
    )
    body = TestClient(app).get("/api/omnigent/codex-catalog-readiness").json()
    assert "exact_image" in body["admissionReadiness"]["blocking"]
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_healthy_generic_endpoint_does_not_infer_provider_capabilities(monkeypatch):
    body = (
        TestClient(
            _app(
                monkeypatch,
                session=_Session([_profile()], latest_observation_at=None),
            )
        )
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    assert body["available"] is False
    assert set(body["admissionReadiness"]["blocking"]) >= {
        "provider_snapshot",
        "event_transport",
        "observation_freshness",
    }
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_configured_images_do_not_replace_observed_deployment_manifest(monkeypatch):
    body = (
        TestClient(
            _app(
                monkeypatch,
                session=_Session(
                    [_profile()],
                    bridge_sessions=[
                        _attested_bridge_session(server_digest="9", host_digest="8")
                    ],
                ),
            )
        )
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    assert body["available"] is False
    assert set(body["admissionReadiness"]["blocking"]) >= {
        "server_build",
        "ui_build",
        "host_build",
        "exact_image",
    }
    assert body["admissionReadiness"]["allowHistoricalReads"] is True
    assert body["admissionReadiness"]["allowCleanup"] is True


def test_stale_authenticated_bootstrap_evidence_fails_closed(monkeypatch):
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", "canary-secret")
    client = TestClient(
        _app(
            monkeypatch,
            session=_Session(
                [_profile()],
                bridge_sessions=(),
                latest_observation_at=None,
            ),
        )
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", raising=False)
    monkeypatch.delenv("MOONMIND_SOURCE_COMMIT", raising=False)

    body = client.get(
        "/api/omnigent/codex-catalog-readiness",
        headers={
            "X-MoonMind-Acceptance-Canary": "canary-secret",
            catalog._BOOTSTRAP_EVIDENCE_HEADER: _bootstrap_header(
                observed_at=datetime.now(UTC) - timedelta(minutes=6)
            ),
        },
    ).json()

    assert body["available"] is False
    assert set(body["admissionReadiness"]["blocking"]) >= {
        "provider_snapshot",
        "event_transport",
        "server_build",
        "host_build",
        "protected_live_evidence",
    }


@pytest.mark.asyncio
async def test_live_readiness_requires_worker_route_backend_and_network(monkeypatch):
    responses = iter(
        [
            _HealthResponse(),
            _HealthResponse(
                {
                    "ready": True,
                    "buildId": "build-1",
                    "registryFingerprint": "sha256:registry",
                    "immutableReleaseIdentity": True,
                    "taskQueues": ["mm.activity.agent_runtime"],
                    "activityTypes": [
                        "agent_runtime.reconcile_managed_sessions",
                        "integration.omnigent.oauth_host_janitor",
                    ],
                    "containerBackend": {
                        "ready": True,
                        "enforcedNetworkRefs": [OMNIGENT_EGRESS_NETWORK_REF],
                        "enforcedEgressProfileRefs": [OMNIGENT_EGRESS_PROFILE.ref],
                    },
                }
            ),
            _HealthResponse(
                {
                    "ready": True,
                    "buildId": "build-1",
                    "registryFingerprint": "sha256:workflow-registry",
                    "immutableReleaseIdentity": True,
                    "taskQueues": ["mm.workflow"],
                    "workflowTypes": ["MoonMind.AgentSession"],
                    "activityTypes": [],
                }
            ),
        ]
    )

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return next(responses)

    monkeypatch.setattr(catalog.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(catalog, "resolved_server_url", lambda: "http://omnigent")

    assert (
        await catalog._live_deployment_readiness()
        == catalog.LiveDeploymentReadiness(
            endpoint_ready=True,
            backend_ready=True,
            enforced_network_refs=frozenset({OMNIGENT_EGRESS_NETWORK_REF}),
            enforced_egress_profile_refs=frozenset({OMNIGENT_EGRESS_PROFILE.ref}),
            workflow_types=frozenset({"MoonMind.AgentSession"}),
            activity_types=frozenset(
                {
                    "agent_runtime.reconcile_managed_sessions",
                    "integration.omnigent.oauth_host_janitor",
                }
            ),
            immutable_worker_build=True,
        )
    )


def test_static_policy_requires_live_connected_host_lease(monkeypatch):
    binding = SimpleNamespace(
        provider_profile_id="codex-oauth", static_host_id="opaque"
    )
    stale_lease = SimpleNamespace(
        provider_profile_id="codex-oauth",
        status="ready",
        host_readiness="ready",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        disconnected_at=None,
    )
    body = (
        TestClient(
            _app(
                monkeypatch,
                session=_Session(
                    [_profile()], bindings=[binding], host_leases=[stale_lease]
                ),
            )
        )
        .get("/api/omnigent/codex-catalog-readiness")
        .json()
    )

    profile = body["executionProfiles"][0]
    assert "static_host_not_ready" not in {
        reason["code"] for reason in profile["gateReasons"]
    }
    assert body["hostModes"] == ["on_demand_docker"]


def test_catalog_denies_caller_without_provider_profile_permission(monkeypatch):
    response = TestClient(_app(monkeypatch, session=_Session([]), superuser=False)).get(
        "/api/omnigent/codex-catalog-readiness"
    )
    assert response.status_code == 403


# --- Exact-artifact + freshness support gating (MoonLadderStudios/MoonMind#3710 AC10) -


_EXACT_COMMIT = "abc123"


def _write_exact_artifact_evidence(tmp_path, *, commit=_EXACT_COMMIT, verdict="passed"):
    from moonmind.omnigent.exact_artifact_conformance import (
        EXACT_ARTIFACT_CONFORMANCE_VERSION,
    )

    path = tmp_path / "exact-artifact.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": EXACT_ARTIFACT_CONFORMANCE_VERSION,
                "sourceCommit": commit,
                "verdict": verdict,
                "failures": (
                    [] if verdict == "passed" else [{"code": "x", "detail": "y"}]
                ),
                "images": {
                    "server": "img@sha256:" + "a" * 64,
                    "worker": "img@sha256:" + "b" * 64,
                    "ui": "img@sha256:" + "c" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_live_projection(
    tmp_path,
    *,
    commit=_EXACT_COMMIT,
    ready=True,
    generated_ago=timedelta(minutes=5),
    expires_in=timedelta(days=20),
    schema_version=None,
    filename="live-projection.json",
):
    from moonmind.omnigent.live_verification_health import (
        LIVE_VERIFICATION_HEALTH_VERSION,
    )

    now = datetime.now(UTC)
    path = tmp_path / filename
    path.write_text(
        json.dumps(
            {
                "schemaVersion": schema_version or LIVE_VERIFICATION_HEALTH_VERSION,
                "rolloutReady": ready,
                "deployedCommit": commit,
                "generatedAt": (now - generated_ago).isoformat(),
                "acceptanceExpiresAt": (now + expires_in).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return path


def _configure_support_evidence(monkeypatch, tmp_path):
    """Point every support-evidence env var at fresh, passing artifacts."""
    monkeypatch.setattr(catalog, "validate_acceptance_manifest", lambda *a, **k: None)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_SOURCE_COMMIT", _EXACT_COMMIT)
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", str(acceptance))
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE",
        str(_write_exact_artifact_evidence(tmp_path)),
    )
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(_write_live_projection(tmp_path)),
    )


def test_support_reasons_clean_with_fresh_exact_artifact_and_live_evidence(
    monkeypatch, tmp_path
):
    _configure_support_evidence(monkeypatch, tmp_path)
    assert catalog._support_reasons() == []


def test_support_reasons_flag_missing_exact_artifact_evidence(monkeypatch, tmp_path):
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.delenv("MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE", raising=False)
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "exact_artifact_evidence_unavailable" in codes
    assert "acceptance_evidence_unavailable" not in codes


def test_support_reasons_flag_failed_exact_artifact_verdict(monkeypatch, tmp_path):
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE",
        str(_write_exact_artifact_evidence(tmp_path, verdict="failed")),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "exact_artifact_evidence_unavailable" in codes


def test_support_reasons_flag_exact_artifact_commit_mismatch(monkeypatch, tmp_path):
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE",
        str(_write_exact_artifact_evidence(tmp_path, commit="different")),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "exact_artifact_evidence_unavailable" in codes


def test_support_reasons_flag_stale_live_verification(monkeypatch, tmp_path):
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(_write_live_projection(tmp_path, ready=False)),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "live_verification_stale" in codes


def test_support_reasons_flag_live_verification_commit_mismatch(monkeypatch, tmp_path):
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(_write_live_projection(tmp_path, commit="different")),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "live_verification_stale" in codes


def test_support_reasons_canary_bypasses_all_support_evidence(monkeypatch, tmp_path):
    # An in-progress acceptance canary is exempt from support gating so it can
    # produce the very evidence the gate consumes.
    monkeypatch.delenv("MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE", raising=False)
    monkeypatch.delenv("MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION", raising=False)
    assert catalog._support_reasons(acceptance_canary=True) == []


def test_support_reasons_flag_expired_acceptance_window(monkeypatch, tmp_path):
    """A once-ready projection stops being accepted when its manifest expires."""
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(
            _write_live_projection(
                tmp_path,
                expires_in=-timedelta(minutes=1),
                filename="expired-projection.json",
            )
        ),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "live_verification_stale" in codes


def test_support_reasons_flag_projection_whose_publisher_stopped(monkeypatch, tmp_path):
    """Scheduled monitoring stopping must not leave a stale ready verdict."""
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(
            _write_live_projection(
                tmp_path,
                generated_ago=timedelta(days=2),
                filename="stale-projection.json",
            )
        ),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "live_verification_stale" in codes


def test_support_reasons_flag_unversioned_projection(monkeypatch, tmp_path):
    """An unversioned or foreign-schema document is not authoritative."""
    _configure_support_evidence(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION",
        str(
            _write_live_projection(
                tmp_path,
                schema_version="something.else/v9",
                filename="unversioned-projection.json",
            )
        ),
    )
    codes = {reason.code for reason in catalog._support_reasons()}
    assert "live_verification_stale" in codes
