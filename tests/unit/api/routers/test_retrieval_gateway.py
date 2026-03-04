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
from moonmind.workflows.agent_queue.service import AgentQueueAuthenticationError


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
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = (
        lambda: RetrievalAuthContext(
            auth_source="worker_token",
            allowed_repositories=("allowed/repo",),
            capabilities=("rag",),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "other/repo"}},
        )

    assert response.status_code == 403


def test_context_returns_gateway_transport_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = (
        lambda: RetrievalAuthContext(
            auth_source="worker_token",
            allowed_repositories=("moonmind",),
            capabilities=("rag",),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "moonmind"}},
        )

    assert response.status_code == 200
    assert response.json()["transport"] == "gateway"


class MockQueueService:
    def __init__(self, policy=None, error=None) -> None:
        self.policy = policy
        self.error = error
        self.called_with_token = None

    async def resolve_worker_token(self, token: str):
        self.called_with_token = token
        if self.error:
            raise self.error
        return self.policy


@pytest.mark.asyncio
async def test_authorize_with_worker_token_header() -> None:
    policy = SimpleNamespace(
        auth_source="worker_token",
        allowed_repositories=("repo1",),
        capabilities=("rag",),
    )
    service = MockQueueService(policy=policy)

    result = await authorize_retrieval_request(
        worker_token_header="token_abc",
        authorization_header=None,
        queue_service=service,  # type: ignore
        user=None,
    )

    assert result.auth_source == "worker_token"
    assert result.allowed_repositories == ("repo1",)
    assert result.capabilities == ("rag",)
    assert service.called_with_token == "token_abc"


@pytest.mark.asyncio
async def test_authorize_with_bearer_token() -> None:
    policy = SimpleNamespace(
        auth_source="worker_token",
        allowed_repositories=("repo2",),
        capabilities=("gateway",),
    )
    service = MockQueueService(policy=policy)

    result = await authorize_retrieval_request(
        worker_token_header=None,
        authorization_header="Bearer token_xyz",
        queue_service=service,  # type: ignore
        user=None,
    )

    assert result.auth_source == "worker_token"
    assert result.allowed_repositories == ("repo2",)
    assert result.capabilities == ("gateway",)
    assert service.called_with_token == "token_xyz"


@pytest.mark.asyncio
async def test_authorize_invalid_worker_token() -> None:
    service = MockQueueService(error=AgentQueueAuthenticationError("invalid"))

    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header="invalid_token",
            authorization_header=None,
            queue_service=service,  # type: ignore
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid worker token."


@pytest.mark.asyncio
async def test_authorize_missing_capability() -> None:
    policy = SimpleNamespace(
        auth_source="worker_token",
        allowed_repositories=("repo3",),
        capabilities=("other_capability",),
    )
    service = MockQueueService(policy=policy)

    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header="token_123",
            authorization_header=None,
            queue_service=service,  # type: ignore
            user=None,
        )

    assert excinfo.value.status_code == 403
    assert (
        excinfo.value.detail == "Worker token does not have RAG retrieval capability."
    )


@pytest.mark.asyncio
async def test_authorize_with_valid_user() -> None:
    user = SimpleNamespace(id="user_1")
    service = MockQueueService()

    result = await authorize_retrieval_request(
        worker_token_header=None,
        authorization_header=None,
        queue_service=service,  # type: ignore
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"
    assert result.allowed_repositories == ()
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_unauthorized() -> None:
    service = MockQueueService()

    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            worker_token_header=None,
            authorization_header=None,
            queue_service=service,  # type: ignore
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Retrieval authentication is required."
