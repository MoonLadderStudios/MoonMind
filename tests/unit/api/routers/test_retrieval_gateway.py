from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_service.api.routers.retrieval_gateway import (
    IssueRetrievalCapabilityRequest,
    RetrievalAuthContext,
    RetrievalBudgetSnapshot,
    RetrievalCorrelation,
    RetrievalQuery,
    SessionRetrievalCapabilityRegistry,
    authorize_retrieval_request,
    get_retrieval_service,
    router,
)
from moonmind.rag.context_pack import ContextItem, build_context_pack
from moonmind.rag.qdrant_client import IndexCollectionHealth, IndexHealthSummary


class StubSettings:
    similarity_top_k = 3

    def __init__(self, *, executable: bool = True, reason: str = "ok") -> None:
        self.executable = executable
        self.reason = reason

    def retrieval_execution_reason(self, source, *, preferred_transport=None):
        _ = source, preferred_transport
        return self.executable, self.reason


class StubService:
    def __init__(self, *, executable: bool = True, reason: str = "ok") -> None:
        self.settings = StubSettings(executable=executable, reason=reason)
        self.calls: list[dict[str, object]] = []
        self.qdrant_client = SimpleNamespace(index_health=self.index_health)

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

    def index_health(self) -> IndexHealthSummary:
        return IndexHealthSummary(
            generated_at="2026-06-01T00:00:00+00:00",
            total_collections=2,
            total_points=9,
            collections=[
                IndexCollectionHealth(
                    name="moonmind-docs",
                    status="green",
                    points_count=7,
                    indexed_vectors_count=7,
                    segments_count=1,
                    vector_size=768,
                    vector_distance="Cosine",
                    freshness_at="2026-05-31T23:00:00+00:00",
                    freshness_source="indexed_at",
                    freshness_status="known",
                ),
                IndexCollectionHealth(
                    name="empty-overlay",
                    status="green",
                    points_count=2,
                    indexed_vectors_count=2,
                    segments_count=1,
                    vector_size=768,
                    vector_distance="Cosine",
                    freshness_at=None,
                    freshness_source=None,
                    freshness_status="unknown",
                ),
            ],
        )

    def collection_health(self):
        return {
            "status": "ok",
            "collections": [
                {
                    "name": "test_collection",
                    "status": "green",
                    "points_count": 2,
                    "vectors_count": 2,
                    "indexed_vectors_count": 2,
                    "dimensions": 768,
                    "freshness": "ready",
                    "error": None,
                }
            ],
        }


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


def test_index_health_returns_collection_counts_and_freshness() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.get("/retrieval/index-health")

    assert response.status_code == 200
    body = response.json()
    assert body["generatedAt"] == "2026-06-01T00:00:00+00:00"
    assert body["totalCollections"] == 2
    assert body["totalPoints"] == 9
    assert body["collections"][0] == {
        "name": "moonmind-docs",
        "status": "green",
        "pointsCount": 7,
        "indexedVectorsCount": 7,
        "segmentsCount": 1,
        "vectorSize": 768,
        "vectorDistance": "Cosine",
        "freshnessAt": "2026-05-31T23:00:00+00:00",
        "freshnessSource": "indexed_at",
        "freshnessStatus": "known",
    }


def test_health_reports_collection_metadata() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.get("/retrieval/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["collections"][0]["name"] == "test_collection"
    assert body["collections"][0]["points_count"] == 2
    assert body["collections"][0]["dimensions"] == 768


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


def _correlation(**overrides) -> RetrievalCorrelation:
    values = {
        "workflow_id": "workflow-1",
        "step_execution_id": "step-1",
        "bridge_session_id": "bridge-1",
        "omnigent_session_id": "session-1",
        "host_id": "host-1",
        "turn_id": "turn-1",
        "tool_call_id": "tool-1",
    }
    values.update(overrides)
    return RetrievalCorrelation(**values)


def _capability_request(**budget_overrides) -> IssueRetrievalCapabilityRequest:
    budget = {
        "tenant_id": "tenant-1",
        "repository": "moonmind",
        "run_id": "run-1",
        "workspace_id": "workspace-1",
        "collections": ["repo-main"],
        "top_k": 3,
        "max_tokens": 256,
        "max_latency_ms": 1000,
        "max_queries": 2,
        "overlay_policy": "include",
        "policy_version": "policy-v1",
    }
    budget.update(budget_overrides)
    return IssueRetrievalCapabilityRequest(
        correlation=_correlation(),
        budget=RetrievalBudgetSnapshot(**budget),
        expires_in_seconds=60,
    )


def test_session_capability_is_opaque_host_bound_and_revocable() -> None:
    registry = SessionRetrievalCapabilityRegistry()
    token, capability = registry.issue(_capability_request())

    assert token.startswith("rcap_")
    assert token not in repr(capability)
    assert registry.authorize(
        token, host_id="host-1", session_id="session-1"
    ) is capability

    with pytest.raises(HTTPException) as wrong_host:
        registry.authorize(token, host_id="host-2", session_id="session-1")
    assert wrong_host.value.status_code == 403

    registry.revoke(capability.capability_id, "host_cleanup")
    with pytest.raises(HTTPException) as revoked:
        registry.authorize(token, host_id="host-1", session_id="session-1")
    assert revoked.value.status_code == 401


def test_session_capability_deduplicates_and_accounts_query_budget() -> None:
    registry = SessionRetrievalCapabilityRegistry()
    _token, capability = registry.issue(_capability_request(max_queries=1))
    payload = RetrievalQuery(
        query="bounded query",
        filters={
            "tenant_id": "tenant-1",
            "repository": "moonmind",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
        },
        correlation=_correlation(),
    )

    assert registry.reserve(capability, payload) is None
    response = {"items": [{"source": "a"}]}
    registry.complete(
        capability,
        payload,
        response,
        started_at=datetime.now(tz=UTC),
    )
    assert registry.reserve(capability, payload) == response

    second = payload.model_copy(
        update={"correlation": _correlation(tool_call_id="tool-2")}
    )
    with pytest.raises(HTTPException) as exhausted:
        registry.reserve(capability, second)
    assert exhausted.value.status_code == 429
    diagnostics = registry.diagnostics("workflow-1")
    assert diagnostics["followUpRequestCount"] == 1
    assert diagnostics["requests"][0]["delivery"] == "same_turn"


def test_session_capability_rejects_concurrent_duplicate_and_records_failure() -> None:
    registry = SessionRetrievalCapabilityRegistry()
    _token, capability = registry.issue(_capability_request())
    payload = RetrievalQuery(
        query="bounded query",
        filters={
            "tenant_id": "tenant-1",
            "repository": "moonmind",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
        },
        correlation=_correlation(),
    )

    assert registry.reserve(capability, payload) is None
    with pytest.raises(HTTPException) as duplicate:
        registry.reserve(capability, payload)
    assert duplicate.value.status_code == 409

    registry.terminate(
        capability,
        payload,
        state="failed",
        failure_class="dependency_failure",
        started_at=datetime.now(tz=UTC),
    )
    diagnostics = registry.diagnostics("workflow-1")
    assert diagnostics["requests"][0]["state"] == "failed"
    assert diagnostics["requests"][0]["failureClass"] == "dependency_failure"


def test_session_budget_binds_turn_filters_and_query_bytes() -> None:
    from api_service.api.routers.retrieval_gateway import _enforce_session_budget

    registry = SessionRetrievalCapabilityRegistry()
    _token, capability = registry.issue(
        _capability_request(filters={"repository": "moonmind"}, max_query_bytes=4)
    )
    base = {
        "query": "short",
        "filters": {
            "tenant_id": "tenant-1",
            "repository": "moonmind",
            "run_id": "run-1",
            "workspace_id": "workspace-1",
        },
        "correlation": _correlation(),
    }

    with pytest.raises(HTTPException) as query_too_large:
        _enforce_session_budget(RetrievalQuery(**base), capability)
    assert query_too_large.value.status_code == 413

    wrong_turn = dict(base, query="ok", correlation=_correlation(turn_id="turn-2"))
    with pytest.raises(HTTPException) as turn_denied:
        _enforce_session_budget(RetrievalQuery(**wrong_turn), capability)
    assert turn_denied.value.status_code == 403

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
            "collections": None,
            "transport": "direct",
            "initiation_mode": "session",
            "planning_ref": None,
        }
    ]

def test_context_preserves_requested_collections() -> None:
    app = _build_app()
    service = StubService()
    app.dependency_overrides[get_retrieval_service] = lambda: service
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repo": "moonmind"},
                "collections": ["repo-main", "docs-main", "repo-main"],
            },
        )

    assert response.status_code == 200
    assert service.calls[0]["collections"] == ["repo-main", "docs-main"]

def test_context_rejects_blank_collection_name() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repo": "moonmind"},
                "collections": ["repo-main", " "],
            },
        )

    assert response.status_code == 422
    assert "collection names cannot be blank" in str(response.json()["detail"])


def test_context_forwards_planning_ref_to_service() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth
    service = StubService()
    app.dependency_overrides[get_retrieval_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={
                "query": "q",
                "filters": {"repo": "moonmind"},
                "planning_ref": "bd-123",
            },
        )

    assert response.status_code == 200
    assert service.calls[0]["planning_ref"] == "bd-123"


@pytest.mark.parametrize(
    "filters",
    [
        {"workspace": "workspace-a"},
        {"workspace_id": "workspace-1"},
        {"run": "run-a"},
        {"run_id": "run-1"},
        {"job": "job-a"},
        {"job_id": "job-1"},
        {"tenant": "tenant-a"},
        {"tenant_id": "tenant-1"},
        {"agent_run_id": "agent-run-1"},
        {"agentRunId": "agent-run-2"},
        {
            "repo": "moonmind",
            "workspace_id": "workspace-1",
            "run_id": "run-1",
            "job_id": "job-1",
            "tenant_id": "tenant-1",
        },
    ],
)
def test_context_accepts_supported_session_scope_filters(
    filters: dict[str, str],
) -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth
    service = StubService()
    app.dependency_overrides[get_retrieval_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": filters},
        )

    assert response.status_code == 200
    assert response.json()["filters"] == filters
    assert service.calls[0]["filters"] == filters


def test_context_fails_fast_when_retrieval_unavailable_for_session() -> None:
    app = _build_app()
    app.dependency_overrides[authorize_retrieval_request] = _oidc_auth
    service = StubService(executable=False, reason="rag_disabled")
    app.dependency_overrides[get_retrieval_service] = lambda: service

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

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Retrieval is unavailable for this managed session (reason: rag_disabled)."
    )
    assert service.calls == []


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


def test_context_retrieval_token_allowlist_requires_repository_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_RETRIEVAL_TOKEN", "scoped-token")
    monkeypatch.setenv("MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES", "moonmind")
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context",
            json={"query": "q", "filters": {"job_id": "job-1"}},
            headers={"X-MoonMind-Retrieval-Token": "scoped-token"},
        )

    assert response.status_code == 403
    assert "Repository scope is required" in response.json()["detail"]


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
    assert "scope filter" in str(detail)


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
    assert "workspace_id" in str(detail)

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
