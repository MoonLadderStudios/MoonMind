"""Bootstrap boundary coverage for OpenCode deployment qualification."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import omnigent_bootstrap as bootstrap_router
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.omnigent.bootstrap.models import (
    BootstrapDesired,
    BootstrapRecord,
    BootstrapResolved,
    BootstrapState,
)


def _record(state: BootstrapState = BootstrapState.ready) -> BootstrapRecord:
    return BootstrapRecord(
        bootstrapId="omnigent-opencode-default",
        revision=6,
        state=state,
        desired=BootstrapDesired(
            provider="opencode-go",
            modelDisplayName="Muse Spark 1.2 Contributor",
            effort="xhigh",
            acceptContributorDataUse=True,
        ),
        resolved=BootstrapResolved(
            qualifiedModelId="opencode-go/muse-spark-1.2-contributor",
            displayName="Muse Spark 1.2 Contributor",
        ),
        providerProfileRef="opencode-go-default",
        agentProfileRef="omnigent-opencode-default@27",
        updatedAt=datetime.now(UTC),
    )


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(bootstrap_router.router)

    async def _session():
        yield SimpleNamespace()

    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id="user-1", is_superuser=True
    )
    monkeypatch.setattr(
        bootstrap_router, "_require_bootstrap_permission", lambda _user: None
    )
    return TestClient(app)


def test_retry_requalifies_without_re_submitting_the_api_key(
    client, monkeypatch
) -> None:
    """Recovery republishes evidence from the persisted Provider Profile.

    Deployment evidence admits exactly one support combination, so an Agent
    Profile, image, or catalog change invalidates it and every launch then
    fails admission. Recovery must not depend on the operator still holding
    the API key.
    """

    calls: list[str] = []

    import moonmind.omnigent.bootstrap.controller as controller_module
    import moonmind.omnigent.bootstrap.store as store_module

    monkeypatch.setattr(store_module, "load_bootstrap_record", _record)

    class _Controller:
        def __init__(self, *, session_factory) -> None:
            self._session_factory = session_factory

        async def requalify(self) -> BootstrapRecord:
            calls.append("requalify")
            return _record()

    monkeypatch.setattr(controller_module, "BootstrapController", _Controller)

    response = client.post("/api/omnigent/bootstrap/opencode/retry")

    assert response.status_code == 200
    assert calls == ["requalify"]
    body = response.json()
    assert body["state"] == "ready"
    assert body["providerProfileRef"] == "opencode-go-default"


def test_retry_without_bootstrap_state_reports_not_found(
    client, monkeypatch
) -> None:
    import moonmind.omnigent.bootstrap.store as store_module

    monkeypatch.setattr(store_module, "load_bootstrap_record", lambda: None)

    response = client.post("/api/omnigent/bootstrap/opencode/retry")

    assert response.status_code == 404


def test_retry_before_credentials_reports_actionable_setup_error(
    client, monkeypatch
) -> None:
    import moonmind.omnigent.bootstrap.controller as controller_module
    import moonmind.omnigent.bootstrap.store as store_module

    monkeypatch.setattr(
        store_module,
        "load_bootstrap_record",
        lambda: _record(BootstrapState.not_started),
    )

    class _Controller:
        def __init__(self, *, session_factory) -> None:
            pass

        async def requalify(self) -> BootstrapRecord:
            raise ValueError(
                "OpenCode is not configured yet; submit the API key first"
            )

    monkeypatch.setattr(controller_module, "BootstrapController", _Controller)

    response = client.post("/api/omnigent/bootstrap/opencode/retry")

    assert response.status_code == 422
    assert "submit the API key first" in response.json()["detail"]
