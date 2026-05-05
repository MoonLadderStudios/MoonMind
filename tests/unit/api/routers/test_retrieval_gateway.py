from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_service.api.routers.retrieval_gateway import (
    RetrievalAuthContext,
    authorize_retrieval_request,
    get_retrieval_service,
    router,
)
from moonmind.rag.context_pack import ContextItem, build_context_pack


class StubService:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(similarity_top_k=3)
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return build_context_pack(
            items=[ContextItem(score=0.9, source="src/a.py", text="snippet")],
            filters=kwargs["filters"],
            budgets=kwargs["budgets"],
            usage={"tokens": 8, "latency_ms": 4},
            transport="direct",
            telemetry_id="ctx-id",
            max_chars=1200,
        )


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_retrieval_service] = StubService
    return app


def _oidc_auth() -> RetrievalAuthContext:
    return RetrievalAuthContext(
        auth_source="oidc",
        allowed_repositories=(),
        capabilities=("rag",),
    )


def test_context_requires_authentication() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.post("/retrieval/context", json={"query": "q"})

    assert response.status_code == 401


def test_context_rejects_out_of_scope_repo() -> None:
    """Legacy worker-token requests should fail before retrieval execution."""
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "other/repo"}},
            headers={"X-MoonMind-Worker-Token": "token_abc"},
        )

    assert response.status_code == 410


def test_context_returns_gateway_context_pack_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repo": "moonmind"},
                "top_k": 2,
                "overlay_policy": "include",
                "budgets": {"tokens": 32},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["transport"] == "gateway"
    assert body["filters"]["repo"] == "moonmind"
    assert body["usage"]["latency_ms"] == 4
    assert body["items"][0]["source"] == "src/a.py"

def test_context_accepts_scoped_retrieval_token_and_preserves_request_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_RETRIEVAL_TOKEN", "scoped-token")
    app = _build_app()
    service = StubService()
    app.dependency_overrides[get_retrieval_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repository": "moonmind"},
                "top_k": 7,
                "overlay_policy": "skip",
                "budgets": {"tokens": 512, "latency_ms": 1000},
            },
            headers={"X-MoonMind-Retrieval-Token": "scoped-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["transport"] == "gateway"
    assert body["filters"] == {"repository": "moonmind"}
    assert body["budgets"] == {"tokens": 512, "latency_ms": 1000}
    assert service.calls == [
        {
            "query": "q",
            "filters": {"repository": "moonmind"},
            "top_k": 7,
            "overlay_policy": "skip",
            "budgets": {"tokens": 512, "latency_ms": 1000},
            "transport": "direct",
            "initiation_mode": "session",
        }
    ]


def test_context_retrieval_token_enforces_allowed_repository_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_RETRIEVAL_TOKEN", "scoped-token")
    monkeypatch.setenv("MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES", "moonmind")
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "other/repo"}},
            headers={"X-MoonMind-Retrieval-Token": "scoped-token"},
        )

    assert response.status_code == 403



def test_context_rejects_missing_repository_scope_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "budgets": {"tokens": 32},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "repo" in str(detail)


def test_context_rejects_unsupported_filter_keys_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repo": "moonmind", "branch": "main"},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "branch" in str(detail)

def test_context_rejects_unsupported_budget_keys_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "budgets": {"tokens": 32, "mystery_budget": 4},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "mystery_budget" in str(detail)


# ---- authorize_retrieval_request unit tests ----

@pytest.mark.asyncio
async def test_authorize_worker_token_rejected_after_queue_removal() -> None:
    """Worker tokens are rejected after queue-token removal."""
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header="token_abc",
            retrieval_token_header=None,
            authorization_header=None,
            user=None,
        )

    assert excinfo.value.status_code == 410
    assert "removed" in excinfo.value.detail


@pytest.mark.asyncio
async def test_authorize_rejects_unconfigured_bearer_retrieval_token() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header=None,
            retrieval_token_header=None,
            authorization_header="Bearer token_xyz",
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert "not configured" in excinfo.value.detail


@pytest.mark.asyncio
async def test_authorize_accepts_scoped_retrieval_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_RETRIEVAL_TOKEN", "token_xyz")
    monkeypatch.setenv("MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES", "moonmind,docs")

    result = await authorize_retrieval_request(
        worker_token_header=None,
        retrieval_token_header="token_xyz",
        authorization_header=None,
        user=None,
    )

    assert result.auth_source == "retrieval_token"
    assert result.allowed_repositories == ("moonmind", "docs")
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_with_valid_user() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        worker_token_header=None,
        retrieval_token_header=None,
        authorization_header=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"
    assert result.allowed_repositories == ()
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_prefers_valid_user_over_scoped_token_header() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        worker_token_header=None,
        retrieval_token_header="stale-token",
        authorization_header=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"


@pytest.mark.asyncio
async def test_authorize_prefers_valid_user_over_bearer_retrieval_token_fallback() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        worker_token_header=None,
        retrieval_token_header=None,
        authorization_header="Bearer oidc-token-owned-by-auth-provider",
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"


@pytest.mark.asyncio
async def test_authorize_unauthorized() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header=None,
            retrieval_token_header=None,
            authorization_header=None,
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Retrieval authentication is required."
