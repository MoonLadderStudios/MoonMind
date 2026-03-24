from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_service.api.routers.retrieval_gateway import (
    authorize_retrieval_request,
    get_retrieval_service,
    router,
)
from moonmind.rag.context_pack import ContextItem, build_context_pack


class StubService:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(similarity_top_k=3)

    def retrieve(self, **_kwargs):
        return build_context_pack(
            items=[ContextItem(score=0.9, source="src/a.py", text="snippet")],
            filters={"repo": "moonmind"},
            budgets={},
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


def test_context_requires_authentication() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.post("/retrieval/context", json={"query": "q"})

    assert response.status_code == 401


def test_context_rejects_out_of_scope_repo() -> None:
    """Worker-scoped requests should be rejected with 403 when repo is not permitted."""
    # After Phase 3.5 queue removal, worker tokens are rejected with 401 at the auth level.
    # This test verifies the auth gate behavior directly.
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "other/repo"}},
            headers={"X-MoonMind-Worker-Token": "token_abc"},
        )

    assert response.status_code == 401


# ---- authorize_retrieval_request unit tests ----


@pytest.mark.asyncio
async def test_authorize_worker_token_rejected_after_queue_removal() -> None:
    """Worker tokens are temporarily rejected (Phase 3.5 queue removal stub)."""
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header="token_abc",
            authorization_header=None,
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert "temporarily unavailable" in excinfo.value.detail


@pytest.mark.asyncio
async def test_authorize_bearer_token_rejected_after_queue_removal() -> None:
    """Bearer tokens are also rejected (Phase 3.5 stub)."""
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header=None,
            authorization_header="Bearer token_xyz",
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert "temporarily unavailable" in excinfo.value.detail


@pytest.mark.asyncio
async def test_authorize_with_valid_user() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        worker_token_header=None,
        authorization_header=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"
    assert result.allowed_repositories == ()
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_unauthorized() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header=None,
            authorization_header=None,
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Retrieval authentication is required."
