"""MoonLadderStudios/MoonMind#3451 catalog boundary coverage."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from moonmind.security.egress import (
    OMNIGENT_EGRESS_NETWORK_REF,
    OMNIGENT_EGRESS_PROFILE,
)

from api_service.api.routers import omnigent_catalog as catalog
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def all(self):
        return self._rows


class _Session:
    def __init__(self, profiles, *, slots=(), bindings=(), host_leases=(), policies=()):
        self._results = iter((
            _Result(profiles), _Result(slots), _Result(bindings),
            _Result(host_leases), _Result(policies),
        ))

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
    monkeypatch.setattr(catalog, "_secret_ref_results_for_rows", lambda rows: {r.profile_id: {} for r in rows})

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
        )

    monkeypatch.setattr(catalog, "_live_deployment_readiness", live_readiness)
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "registry.test/server@sha256:" + "1" * 64)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", "registry.test/host@sha256:" + "2" * 64)
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent:8000")
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", "/evidence/matrix.json")
    monkeypatch.setenv("MOONMIND_SOURCE_COMMIT", "abc123")
    monkeypatch.setattr(catalog.Path, "read_text", lambda *_args, **_kwargs: "{}")
    monkeypatch.setattr(catalog, "validate_acceptance_manifest", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(catalog.router)
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=None, is_superuser=superuser
    )
    app.dependency_overrides[get_async_session] = lambda: session
    return app


def test_ready_catalog_lists_only_launch_ready_codex_oauth_profiles(monkeypatch):
    profiles = [
        _profile(),
        _profile(profile_id="api-key", credential_source=SimpleNamespace(value="secret_ref")),
    ]
    client = TestClient(_app(monkeypatch, session=_Session(profiles)))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    assert response.status_code == 200
    assert response.json()["available"] is True


def test_protected_first_run_canary_uses_normal_catalog_without_published_manifest(
    monkeypatch,
):
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", "canary-secret")
    client = TestClient(_app(monkeypatch, session=_Session([_profile()])))
    monkeypatch.delenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", raising=False)
    monkeypatch.delenv("MOONMIND_SOURCE_COMMIT", raising=False)

    response = client.get(
        "/api/omnigent/codex-catalog-readiness",
        headers={"X-MoonMind-Acceptance-Canary": "canary-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "acceptance_evidence_unavailable" not in {
        reason["code"] for reason in payload["gateReasons"]
    }


def test_first_run_canary_rejects_an_untrusted_header(monkeypatch):
    monkeypatch.setenv("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", "canary-secret")
    client = TestClient(_app(monkeypatch, session=_Session([_profile()])))
    monkeypatch.delenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", raising=False)
    monkeypatch.delenv("MOONMIND_SOURCE_COMMIT", raising=False)

    response = client.get(
        "/api/omnigent/codex-catalog-readiness",
        headers={"X-MoonMind-Acceptance-Canary": "wrong"},
    )

    body = response.json()
    assert "acceptance_evidence_unavailable" in {
        reason["code"] for reason in body["supportGateReasons"]
    }
    assert body["schemaVersion"] == "moonmind.omnigent-codex-readiness.v2"
    assert body["available"] is True
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
    assert body["eligibleProviderProfiles"] == [{
        "profileId": "codex-oauth",
        "label": "OpenAI subscription",
        "providerId": "openai",
        "runtimeId": "codex_cli",
        "busy": False,
        "queueWhenBusy": True,
    }]
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
    client = TestClient(_app(
        monkeypatch, session=_Session([_profile()], host_leases=[lease])
    ))

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

    body = TestClient(_app(monkeypatch, session=_Session(profiles))).get(
        "/api/omnigent/codex-catalog-readiness"
    ).json()

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
    client = TestClient(_app(
        monkeypatch, session=_Session([profile]), enabled=False
    ))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    body = response.json()
    assert body["available"] is False
    assert {reason["code"] for reason in body["gateReasons"]} >= {
        "bridge_disabled"
    }
    assert all(reason["message"] and reason["remediationHref"] for reason in body["gateReasons"])
    assert secret not in response.text
    for forbidden in ("volume", "hostId", "docker.sock", "token=", "header", "environment"):
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
    body = TestClient(_app(
        monkeypatch, session=_Session([profile], slots=[slot])
    )).get("/api/omnigent/codex-catalog-readiness").json()

    assert body["eligibleProviderProfiles"][0]["busy"] is True
    assert body["eligibleProviderProfiles"][0]["queueWhenBusy"] is True


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"OMNIGENT_ENABLED": "false"}, "rollout_gate_disabled"),
        ({"OMNIGENT_SERVER_URL": ""}, "bridge_endpoint_unavailable"),
        ({"OMNIGENT_SERVER_URL": "omnigent:8000"}, "bridge_endpoint_unavailable"),
        ({"MOONMIND_WORKSPACE_RESOLVER_ENABLED": "false"}, "workspace_resolver_unavailable"),
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
def test_catalog_reports_missing_acceptance_evidence_without_blocking_launch(
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

    assert body["available"] is True
    assert "acceptance_evidence_unavailable" in {
        reason["code"] for reason in body["supportGateReasons"]
    }
    assert "acceptance_evidence_unavailable" not in {
        reason["code"] for reason in body["gateReasons"]
    }


def test_catalog_rejects_placeholder_image_digests(monkeypatch):
    app = _app(monkeypatch, session=_Session([_profile()]))
    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF", "registry.test/server@sha256:" + "0" * 64
    )

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

    body = TestClient(_app(
        monkeypatch,
        session=_Session(
            [_profile()],
            policies=[(identity, version(1)), (identity, version(2))],
        ),
    )).get("/api/omnigent/codex-catalog-readiness").json()

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
    monkeypatch.setattr(catalog, "_require_provider_profile_permission", lambda *_: None)

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
                enforced_egress_profile_refs=frozenset(
                    {OMNIGENT_EGRESS_PROFILE.ref}
                ),
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


@pytest.mark.asyncio
async def test_live_readiness_requires_worker_route_backend_and_network(monkeypatch):
    responses = iter([
        _HealthResponse(),
        _HealthResponse({
            "ready": True,
            "taskQueues": ["mm.activity.agent_runtime"],
            "containerBackend": {
                "ready": True,
                "enforcedNetworkRefs": [OMNIGENT_EGRESS_NETWORK_REF],
                "enforcedEgressProfileRefs": [OMNIGENT_EGRESS_PROFILE.ref],
            },
        }),
    ])

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

    assert await catalog._live_deployment_readiness() == catalog.LiveDeploymentReadiness(
        endpoint_ready=True,
        backend_ready=True,
        enforced_network_refs=frozenset({OMNIGENT_EGRESS_NETWORK_REF}),
        enforced_egress_profile_refs=frozenset({OMNIGENT_EGRESS_PROFILE.ref}),
    )


def test_static_policy_requires_live_connected_host_lease(monkeypatch):
    binding = SimpleNamespace(provider_profile_id="codex-oauth", static_host_id="opaque")
    stale_lease = SimpleNamespace(
        provider_profile_id="codex-oauth",
        status="ready",
        host_readiness="ready",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        disconnected_at=None,
    )
    body = TestClient(_app(
        monkeypatch,
        session=_Session([_profile()], bindings=[binding], host_leases=[stale_lease]),
    )).get("/api/omnigent/codex-catalog-readiness").json()

    profile = body["executionProfiles"][0]
    assert "static_host_not_ready" not in {
        reason["code"] for reason in profile["gateReasons"]
    }
    assert body["hostModes"] == ["on_demand_docker"]


def test_catalog_denies_caller_without_provider_profile_permission(monkeypatch):
    response = TestClient(_app(
        monkeypatch, session=_Session([]), superuser=False
    )).get("/api/omnigent/codex-catalog-readiness")
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
                "failures": [] if verdict == "passed" else [{"code": "x", "detail": "y"}],
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


def _write_live_projection(tmp_path, *, commit=_EXACT_COMMIT, ready=True):
    path = tmp_path / "live-projection.json"
    path.write_text(
        json.dumps({"rolloutReady": ready, "deployedCommit": commit}),
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
