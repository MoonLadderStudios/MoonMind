from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
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
    app.dependency_overrides[get_retrieval_service] = lambda: StubService()
    return app


def test_context_requires_authentication() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.post("/retrieval/context", json={"query": "q"})

    assert response.status_code == 401


def test_context_rejects_out_of_scope_repo() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = lambda: RetrievalAuthContext(
        auth_source="worker_token",
        allowed_repositories=("allowed/repo",),
        capabilities=("rag",),
    )

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "other/repo"}},
        )

    assert response.status_code == 403


def test_context_returns_gateway_transport_for_authorized_request() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = lambda: RetrievalAuthContext(
        auth_source="worker_token",
        allowed_repositories=("moonmind",),
        capabilities=("rag",),
    )

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"repo": "moonmind"}},
        )

    assert response.status_code == 200
    assert response.json()["transport"] == "gateway"
