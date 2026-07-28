"""MoonLadderStudios/MoonMind#3451 catalog boundary coverage."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

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
        return True, {"local-network"}

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

    assert "acceptance_evidence_unavailable" in {
        reason["code"] for reason in response.json()["gateReasons"]
    }
    body = response.json()
    assert body["schemaVersion"] == "moonmind.omnigent-codex-readiness.v1"
    assert body["available"] is False
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
        "images": {},
        "architectures": [],
        "thresholds": {},
        "evidenceRefs": [],
        "blockers": [],
        "directLaunchAllowed": True,
    }
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
def test_catalog_fails_closed_without_qualifying_acceptance_evidence(
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
        ((False, {"local-network"}), "bridge_endpoint_unavailable"),
        ((True, set()), "network_policy_unavailable"),
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
                "enforcedNetworkRefs": ["local-network"],
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

    assert await catalog._live_deployment_readiness() == (
        True,
        {"local-network"},
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
    assert "static_host_not_ready" in {reason["code"] for reason in profile["gateReasons"]}
    assert body["hostModes"] == ["on_demand_docker"]


def test_catalog_denies_caller_without_provider_profile_permission(monkeypatch):
    response = TestClient(_app(
        monkeypatch, session=_Session([]), superuser=False
    )).get("/api/omnigent/codex-catalog-readiness")
    assert response.status_code == 403
