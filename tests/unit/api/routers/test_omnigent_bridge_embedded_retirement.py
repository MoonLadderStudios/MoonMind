"""Router tests for the retired embedded host transport.

MoonLadderStudios/MoonMind#3955: new embedded-transport admission (session
creation, host registration) is rejected with an explicit retired error that
names the supported proxy alternative and creates no host, session, or
credential consumer. Existing-session continuity (reads, controls, typed
cleanup) and the native Workflow Chat ``embedded=1`` presentation option are
unaffected.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    OMNIGENT_BRIDGE_MOUNT_PATH,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_create_embedded_facade,
    _get_embedded_host_facade,
    _get_execution_service,
    _get_launch_default_agent_selection,
    _require_bridge_enabled,
    _require_embedded_mode,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    parse_bridge_config,
)
from tests.unit.api.routers.test_omnigent_bridge import (
    _USER_ID,
    _CREATE_PATH,
    _READINESS_PATH,
    _FakeEmbeddedFacade,
    _FakeService,
    _create_body,
    _fake_store_dependency,
    _mock_user,
)

_REGISTER_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/hosts/register"


def _embedded_config() -> Any:
    # Disabled embedded still parses (retained rows must drain and decode);
    # the tests override the enablement gate and exercise the retired
    # admission boundary. Enabled embedded fails fast at parse by design.
    return parse_bridge_config(
        {
            "enabled": False,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )


async def _passed_evidence(
    _config: Any, **_kwargs: Any
) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "status": "passed",
            "supportedHostModes": ["static_compose", "on_demand_docker"],
        }
        for key in ("proxyConformance", "liveSmoke", "hostAuthConformance")
    }


def test_new_embedded_session_creates_nothing_and_names_proxy() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = _FakeEmbeddedFacade()
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = _embedded_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
    app.dependency_overrides[_get_bridge_store] = _fake_store_dependency
    app.dependency_overrides[_get_launch_default_agent_selection] = lambda: None

    response = TestClient(app).post(
        _CREATE_PATH,
        json=_create_body(host_type="external", host_id="host-1", workspace="/repo"),
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["code"] == "omnigent_embedded_transport_retired"
    assert detail["supportedAlternative"] == HOST_PROTOCOL_MODE_PROXY
    assert HOST_PROTOCOL_MODE_PROXY in detail["message"]
    # No session or credential consumer was created through the facade.
    assert facade.created == []


def test_new_embedded_host_registration_creates_nothing() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = Mock()
    facade.register_host = AsyncMock()
    app.dependency_overrides[_require_embedded_mode] = _embedded_config
    app.dependency_overrides[_get_embedded_host_facade] = lambda: facade

    response = TestClient(app).post(
        _REGISTER_PATH,
        json={"hostId": "host-1", "capabilities": {}},
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["code"] == "omnigent_embedded_transport_retired"
    assert detail["supportedAlternative"] == HOST_PROTOCOL_MODE_PROXY
    # No host, lease, or credential consumer was created through the facade.
    facade.register_host.assert_not_awaited()


def test_proxy_session_creation_is_unaffected() -> None:
    from tests.unit.api.routers.test_omnigent_bridge import _FakeProxy

    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    proxy = _FakeProxy()
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[_get_bridge_store] = _fake_store_dependency
    app.dependency_overrides[_get_launch_default_agent_selection] = lambda: None

    response = TestClient(app).post(_CREATE_PATH, json=_create_body())

    assert response.status_code == 200
    assert response.json()["id"] == "sess-1"
    assert len(proxy.created) == 1


def test_embedded_readiness_reports_retirement_not_new_capacity(
    monkeypatch,
) -> None:
    module = importlib.import_module("api_service.api.routers.omnigent_bridge")
    monkeypatch.setattr(module, "_resolve_embedded_evidence", _passed_evidence)
    monkeypatch.setattr(
        module,
        "_resolve_bridge_policy_authority",
        AsyncMock(
            return_value={
                "policyRef": "omnigent-codex@1",
                "policyDigest": "sha256:durable-policy",
                "snapshotRef": "omnigent-policy:sha256:durable-snapshot",
                "validation": {"valid": True, "diagnostics": []},
                "boundaries": {
                    "host": {
                        "serverImageRef": "registry.test/server@sha256:" + "1" * 64,
                        "hostImageRef": "registry.test/host@sha256:" + "2" * 64,
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        module,
        "evaluate_active_host_auth_readiness",
        AsyncMock(return_value={"ready": True}),
    )
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_require_bridge_enabled] = _embedded_config

    response = TestClient(app).get(_READINESS_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["selectedMode"] == HOST_PROTOCOL_MODE_EMBEDDED
    retirement = body["retirement"]
    assert retirement["newAdmissionAllowed"] is False
    assert retirement["code"] == "omnigent_embedded_transport_retired"
    assert retirement["supportedAlternative"] == HOST_PROTOCOL_MODE_PROXY
    diagnostics = body["compatibilityDiagnostics"]
    assert diagnostics["retirement"]["newAdmissionAllowed"] is False
    # Operators always get the proxy guidance for a retired selection.
    assert HOST_PROTOCOL_MODE_PROXY in (
        diagnostics["rollbackRecommendation"] or ""
    )


class _RetryCapableStore:
    """Store stand-in with an already-admitted idempotency-key row."""

    def __init__(self, *, admitted: bool = True) -> None:
        self._admitted = admitted

    async def active_host_protocol_modes(self, *, exclude_idempotency_key=None):
        return {}

    async def get_existing(self, idempotency_key: str):
        if self._admitted and idempotency_key == "idem-1":
            from types import SimpleNamespace

            return SimpleNamespace(omnigent_session_id="sess-77")
        return None


def _proxy_config() -> Any:
    return parse_bridge_config(
        {"compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_PROXY}}
    )


def test_embedded_retry_with_admitted_key_reconciles_instead_of_rejecting() -> None:
    """P1: POST /v1/sessions retries reuse the recorded row (no 410, no recreate)."""
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = _FakeEmbeddedFacade()
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = _embedded_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
    app.dependency_overrides[_get_bridge_store] = _RetryCapableStore
    app.dependency_overrides[_get_launch_default_agent_selection] = lambda: None

    response = TestClient(app).post(_CREATE_PATH, json=_create_body())

    assert response.status_code == 200
    assert response.json()["id"] == "sess-77"
    # The retry reconciled the recorded row: nothing was newly admitted.
    assert facade.created == []
    assert facade.attached == ["sess-77"]


def test_new_embedded_admission_rejected_before_proxy_or_facade_errors() -> None:
    """P2: the shared early gate answers 410 before 503/501 dependency errors."""
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)

    async def _stale_evidence_proxy():
        raise HTTPException(status_code=503, detail={"code": "stale_evidence"})

    async def _missing_launch_facade():
        raise HTTPException(status_code=501, detail={"code": "launch_gone"})

    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = _embedded_config
    app.dependency_overrides[_get_bridge_proxy] = _stale_evidence_proxy
    app.dependency_overrides[_get_create_embedded_facade] = _missing_launch_facade
    app.dependency_overrides[_get_bridge_store] = lambda: _RetryCapableStore(
        admitted=False
    )
    app.dependency_overrides[_get_launch_default_agent_selection] = lambda: None

    response = TestClient(app).post(_CREATE_PATH, json=_create_body())

    assert response.status_code == 410
    assert (
        response.json()["detail"]["code"] == "omnigent_embedded_transport_retired"
    )


def test_retained_embedded_row_reads_without_live_transport(monkeypatch) -> None:
    """P1: historical reads decode the retained row when no facade exists."""
    from types import SimpleNamespace

    module = importlib.import_module("api_service.api.routers.omnigent_bridge")

    async def _no_facade(**_kwargs):
        return (None, True)

    monkeypatch.setattr(module, "_resolve_session_control_facade", _no_facade)

    retained_metadata = {
        "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
        "embedded_runner_lifecycle": {"state": "runner_tunnel_ready"},
        "embedded_runner_launch": {"runnerId": "r-1"},
        "egress_cleanup_authority": {"phase": "attested"},
    }
    row = SimpleNamespace(
        status="completed",
        omnigent_agent_id=None,
        omnigent_host_id="host-1",
        omnigent_runner_id="runner-1",
        moonmind_workflow_id="mm:w1",
        moonmind_agent_run_id="ar-1",
        idempotency_key="idem-1",
        bridge_session_id="brs-1",
        terminal_refs={"summary": "done"},
        diagnostics_ref="artifact://omnigent/diag",
        final_snapshot_ref=None,
        metadata_=retained_metadata,
    )

    class _RetainedStore:
        async def get_session_by_provider_session_id(self, session_id: str):
            return row if session_id == "sess-77" else None

        async def get_session_owner(self, session_id: str):
            if session_id == "sess-77":
                return SimpleNamespace(workflow_id="mm:w1", agent_run_id="ar-1")
            return None

    for ban in (
        "endpoint-secret.example",
        "token-secret",
        "host-secret",
        "runner-secret",
    ):
        retained_metadata[ban] = ban
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = _proxy_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: None
    app.dependency_overrides[_get_bridge_store] = lambda: _RetainedStore()

    response = TestClient(app).get(f"{_CREATE_PATH}/sess-77")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "sess-77"
    assert body["retained"] is True
    decoding = body["retainedDecoding"]
    assert decoding["isEmbedded"] is True
    assert decoding["lifecycleState"] == "runner_tunnel_ready"
    rendered = repr(body)
    for banned in ("endpoint-secret.example", "token-secret", "host-secret"):
        assert banned not in rendered
