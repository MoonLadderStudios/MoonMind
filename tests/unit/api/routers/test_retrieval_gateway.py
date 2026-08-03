from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_service.api.routers import omnigent_bridge
from api_service.api.routers.retrieval_gateway import (
    BridgeRetrievalCapabilityIssue,
    RetrievalCapabilityIssue,
    RetrievalAuthContext,
    _bridge_authoritative_issue,
    _server_policy_snapshot,
    issue_bridge_retrieval_capability,
    authorize_retrieval_request,
    bridge_follow_up_retrieval_diagnostics,
    get_capability_registry,
    get_bridge_session_store,
    get_retrieval_service,
    revoke_bridge_retrieval_capabilities,
    router,
)
from api_service.retrieval_capabilities import (
    RetrievalBudgetSnapshot,
    RetrievalCapabilityError,
    RetrievalCapabilityRegistry,
)
from moonmind.omnigent.embedded_host_channel import derive_runner_binding_token
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter
from moonmind.rag.context_pack import ContextItem, build_context_pack
from moonmind.rag.qdrant_client import IndexCollectionHealth, IndexHealthSummary
from omnigent.runner._entry import _resolve_agent_spec_from_server
from omnigent.runner.proxy_mcp_manager import ProxyMcpManager


def _bridge_row(**overrides):
    values = {
        "status": "active",
        "bridge_session_id": "bridge-1",
        "moonmind_workflow_id": "workflow-1",
        "moonmind_agent_run_id": "agent-run-1",
        "omnigent_host_id": "host-1",
        "omnigent_session_id": "session-1",
        "moonmind_run_id": "run-1",
        "step_execution_id": "step-1",
        "workspace": "workspace-1",
        "effective_launch_snapshot_json": {
            "followUpRetrieval": {
                "enabled": True,
                "tenantId": "tenant-1",
                "repository": "MoonMind",
                "policyVersion": "policy-7",
                "collections": ["repo", "docs"],
                "filters": {"branch": "main"},
                "topK": 5,
                "maxContextTokens": 1000,
                "maxLifetimeSeconds": 120,
            }
        },
    }
    values.update(overrides)
    return type("BridgeRow", (), values)()


class _OwnedExecutionService:
    """Execution service whose workflow is owned by ``user-1``."""

    async def describe_execution(self, workflow_id: str):
        assert workflow_id == "workflow-1"
        return SimpleNamespace(owner_id="user-1")


class _UnownedExecutionService:
    """Execution service whose workflow belongs to somebody else."""

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id="user-1")


def _request_stub() -> SimpleNamespace:
    """Minimal Request stand-in for direct dependency calls.

    ``authorize_retrieval_request`` only touches ``request.app.state`` on the
    session-capability branch, so the stub carries just that much state.
    """
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def test_bridge_capability_derives_identity_and_clamps_narrowing() -> None:
    issue = _bridge_authoritative_issue(
        _bridge_row(),
        BridgeRetrievalCapabilityIssue(
            collections=["docs"],
            filters={"branch": "main"},
            lifetime_seconds=600,
            top_k=3,
            max_context_tokens=500,
        ),
    )

    assert issue.tenant_id == "tenant-1"
    assert issue.repository == "MoonMind"
    assert issue.run_id == "run-1"
    assert issue.workspace_id == "workspace-1"
    assert issue.host_id == "host-1"
    assert issue.session_id == "session-1"
    assert issue.collections == ["docs"]
    assert issue.top_k == 3
    assert issue.max_context_tokens == 500
    assert issue.lifetime_seconds == 120


def test_bridge_capability_rejects_caller_scope_broadening() -> None:
    with pytest.raises(HTTPException) as denied:
        _bridge_authoritative_issue(
            _bridge_row(),
            BridgeRetrievalCapabilityIssue(collections=["private"]),
        )
    assert denied.value.status_code == 403

    with pytest.raises(HTTPException) as inactive:
        _bridge_authoritative_issue(
            _bridge_row(status="completed"),
            BridgeRetrievalCapabilityIssue(),
        )
    assert inactive.value.status_code == 409


def test_bridge_capability_lifetime_is_clamped_to_authority_expiry() -> None:
    row = _bridge_row()
    row.effective_launch_snapshot_json["followUpRetrieval"][
        "authorityExpiresAt"
    ] = 1_000_045

    issue = _bridge_authoritative_issue(
        row,
        BridgeRetrievalCapabilityIssue(lifetime_seconds=120),
        now=1_000_000,
    )

    assert issue.lifetime_seconds == 45


def test_bridge_capability_rejects_expired_or_invalid_authority() -> None:
    expired = _bridge_row()
    expired.effective_launch_snapshot_json["followUpRetrieval"][
        "authorityExpiresAt"
    ] = "1970-01-01T00:00:01Z"
    with pytest.raises(HTTPException) as expired_error:
        _bridge_authoritative_issue(
            expired, BridgeRetrievalCapabilityIssue(), now=2
        )
    assert expired_error.value.status_code == 409

    invalid = _bridge_row()
    invalid.effective_launch_snapshot_json["followUpRetrieval"][
        "authorityExpiresAt"
    ] = "not-a-timestamp"
    with pytest.raises(HTTPException) as invalid_error:
        _bridge_authoritative_issue(
            invalid, BridgeRetrievalCapabilityIssue(), now=1
        )
    assert invalid_error.value.status_code == 409


def test_unscoped_capability_issuance_route_is_not_exposed() -> None:
    app = _build_app()
    # Not every entry in ``app.routes`` exposes ``methods`` (mounts and
    # included-router markers do not), so read it defensively.
    paths = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
    }

    assert ("/retrieval/capabilities", "POST") not in paths


@pytest.mark.asyncio
async def test_bridge_lifecycle_revokes_scoped_capabilities_and_emits_event(
    tmp_path,
) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    budget = RetrievalBudgetSnapshot(
        tenant_id="tenant-1",
        repository="MoonMind",
        run_id="run-1",
        workspace_id="workspace-1",
        host_id="host-1",
        session_id="session-1",
        step_id="step-1",
        workflow_id="workflow-1",
        bridge_session_id="bridge-1",
        policy_version="policy-7",
        collections=("repo",),
        filters=(),
    )
    token, capability = registry.issue(budget, lifetime_seconds=60)

    class Store:
        events = []

        async def get_bridge_session(self, bridge_session_id):
            assert bridge_session_id == "bridge-1"
            return _bridge_row()

        async def append_events(self, bridge_session_id, events):
            assert bridge_session_id == "bridge-1"
            self.events.extend(events)

    store = Store()
    result = await revoke_bridge_retrieval_capabilities(
        "bridge-1",
        registry=registry,
        store=store,
        user=SimpleNamespace(id="user-1"),
        service=_OwnedExecutionService(),
    )

    assert result["revokedCapabilityIds"] == [capability.capability_id]
    assert store.events[0]["eventType"] == "retrieval.capabilities.revoked"
    with pytest.raises(RetrievalCapabilityError, match="revoked"):
        registry.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )


@pytest.mark.asyncio
async def test_bridge_lifecycle_revocation_denied_for_non_owner(tmp_path) -> None:
    """Authentication alone must not reach another principal's authority."""
    registry = RetrievalCapabilityRegistry(tmp_path)

    class Store:
        async def get_bridge_session(self, bridge_session_id):
            return _bridge_row()

        async def append_events(self, bridge_session_id, events):  # pragma: no cover
            raise AssertionError("Revocation must not run for a non-owner.")

    with pytest.raises(HTTPException) as denied:
        await revoke_bridge_retrieval_capabilities(
            "bridge-1",
            registry=registry,
            store=Store(),
            user=SimpleNamespace(id="intruder"),
            service=_UnownedExecutionService(),
        )

    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "workflow_ownership_denied"


@pytest.mark.asyncio
async def test_bridge_lifecycle_refuses_incomplete_revocation_scope(tmp_path) -> None:
    """A partial scope must fail closed rather than widen to the whole run."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    sibling = RetrievalBudgetSnapshot(
        tenant_id="tenant-1",
        repository="MoonMind",
        run_id="run-1",
        workspace_id="workspace-1",
        host_id="host-1",
        session_id="session-2",
        step_id="step-2",
        workflow_id="workflow-1",
        bridge_session_id="bridge-2",
        policy_version="policy-7",
        collections=("repo",),
        filters=(),
    )
    sibling_token, _ = registry.issue(sibling, lifetime_seconds=60)

    class Store:
        async def get_bridge_session(self, bridge_session_id):
            return _bridge_row(omnigent_session_id=None, step_execution_id=None)

        async def append_events(self, bridge_session_id, events):  # pragma: no cover
            raise AssertionError("Incomplete scope must not revoke anything.")

    with pytest.raises(HTTPException) as blocked:
        await revoke_bridge_retrieval_capabilities(
            "bridge-1",
            registry=registry,
            store=Store(),
            user=SimpleNamespace(id="user-1"),
            service=_OwnedExecutionService(),
        )

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "retrieval_lifecycle_scope_incomplete"
    # The unrelated sibling session keeps its authority.
    assert registry.resolve(
        sibling_token, host_id="host-1", session_id="session-2", run_id="run-1"
    )


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


def test_capability_budget_is_compiled_against_server_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_COLLECTIONS", "repo,docs")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_MAX_QUERIES", "3")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_MAX_REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_EMBEDDING_TIMEOUT_MS", "700")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_SEARCH_TIMEOUT_MS", "900")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_OVERLAY_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_RETENTION_DAYS", "7")
    snapshot = _server_policy_snapshot(
        RetrievalCapabilityIssue(
            tenant_id="tenant-1",
            repository="MoonMind",
            run_id="run-1",
            workspace_id="workspace-1",
            host_id="host-1",
            session_id="session-1",
            step_id="step-1",
            workflow_id="workflow-1",
            bridge_session_id="bridge-1",
            policy_version="policy-1",
            collections=["docs"],
            max_queries=99,
            fallback_allowed=True,
        )
    )
    assert snapshot.collections == ("docs",)
    assert snapshot.max_queries == 3
    assert snapshot.max_requests_per_minute == 2
    assert snapshot.embedding_timeout_ms == 700
    assert snapshot.search_timeout_ms == 900
    assert snapshot.overlay_max_age_seconds == 120
    assert snapshot.retention_days == 7
    assert snapshot.fallback_allowed is False


def test_capability_budget_rejects_collection_outside_server_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_COLLECTIONS", "docs")
    with pytest.raises(HTTPException) as denied:
        _server_policy_snapshot(
            RetrievalCapabilityIssue(
                tenant_id="tenant-1",
                repository="MoonMind",
                run_id="run-1",
                workspace_id="workspace-1",
                host_id="host-1",
                session_id="session-1",
                step_id="step-1",
                workflow_id="workflow-1",
                bridge_session_id="bridge-1",
                policy_version="policy-1",
                collections=["private"],
            )
        )
    assert denied.value.status_code == 403


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
            # A non-session token carries no capability, so run identity and
            # stage deadlines stay unset and the service keeps its defaults.
            "run_id": None,
            "overlay_max_age_seconds": None,
            "stale_overlay_allowed": False,
            "embedding_timeout_ms": None,
            "search_timeout_ms": None,
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
            _request_stub(),  # type: ignore[arg-type]
            worker_token_header="token_abc",
            retrieval_token_header=None,
            authorization_header=None,
            host_id=None,
            session_id=None,
            run_id=None,
            step_id=None,
            user=None,
        )

    assert excinfo.value.status_code == 410
    assert "removed" in excinfo.value.detail


@pytest.mark.asyncio
async def test_authorize_rejects_unconfigured_bearer_retrieval_token() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            _request_stub(),  # type: ignore[arg-type]
            worker_token_header=None,
            retrieval_token_header=None,
            authorization_header="Bearer token_xyz",
            host_id=None,
            session_id=None,
            run_id=None,
            step_id=None,
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
        _request_stub(),  # type: ignore[arg-type]
        worker_token_header=None,
        retrieval_token_header="token_xyz",
        authorization_header=None,
        host_id=None,
        session_id=None,
        run_id=None,
        step_id=None,
        user=None,
    )

    assert result.auth_source == "retrieval_token"
    assert result.allowed_repositories == ("moonmind", "docs")
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_with_valid_user() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        _request_stub(),  # type: ignore[arg-type]
        worker_token_header=None,
        retrieval_token_header=None,
        authorization_header=None,
        host_id=None,
        session_id=None,
        run_id=None,
        step_id=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"
    assert result.allowed_repositories == ()
    assert result.capabilities == ("rag",)


@pytest.mark.asyncio
async def test_authorize_prefers_valid_user_over_scoped_token_header() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        _request_stub(),  # type: ignore[arg-type]
        worker_token_header=None,
        retrieval_token_header="stale-token",
        authorization_header=None,
        host_id=None,
        session_id=None,
        run_id=None,
        step_id=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"


@pytest.mark.asyncio
async def test_authorize_prefers_valid_user_over_bearer_retrieval_token_fallback() -> None:
    user = SimpleNamespace(id="user_1")

    result = await authorize_retrieval_request(
        _request_stub(),  # type: ignore[arg-type]
        worker_token_header=None,
        retrieval_token_header=None,
        authorization_header="Bearer oidc-token-owned-by-auth-provider",
        host_id=None,
        session_id=None,
        run_id=None,
        step_id=None,
        user=user,  # type: ignore
    )

    assert result.auth_source == "oidc"


@pytest.mark.asyncio
async def test_authorize_unauthorized() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await authorize_retrieval_request(
            _request_stub(),  # type: ignore[arg-type]
            worker_token_header=None,
            retrieval_token_header=None,
            authorization_header=None,
            host_id=None,
            session_id=None,
            run_id=None,
            step_id=None,
            user=None,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Retrieval authentication is required."


# ---- session-capability boundary tests ----

_SESSION_HEADERS = {
    "X-MoonMind-Host-Id": "host-1",
    "X-MoonMind-Session-Id": "session-1",
    "X-MoonMind-Run-Id": "run-1",
    "X-MoonMind-Step-Id": "step-1",
}


def _session_budget(**overrides) -> RetrievalBudgetSnapshot:
    values = dict(
        tenant_id="tenant-1",
        repository="MoonMind",
        run_id="run-1",
        workspace_id="workspace-1",
        host_id="host-1",
        session_id="session-1",
        step_id="step-1",
        workflow_id="workflow-1",
        bridge_session_id="bridge-1",
        policy_version="policy-7",
        collections=("repo",),
        filters=(),
    )
    values.update(overrides)
    return RetrievalBudgetSnapshot(**values)


def _session_query(**overrides) -> dict:
    body = {
        "query": "bounded query",
        "filters": {"repo": "MoonMind"},
        "collections": ["repo"],
        "correlation": {
            "workflow_id": "workflow-1",
            "step_id": "step-1",
            "bridge_session_id": "bridge-1",
            "omnigent_session_id": "session-1",
            "turn_id": "turn-1",
            "tool_call_id": "tool-1",
        },
    }
    body.update(overrides)
    return body


def _session_app(tmp_path, service: StubService, **budget_overrides):
    app = _build_app()
    app.dependency_overrides[get_retrieval_service] = lambda: service
    registry = RetrievalCapabilityRegistry(tmp_path)
    app.state.retrieval_capability_registry = registry
    token, capability = registry.issue(
        _session_budget(**budget_overrides), lifetime_seconds=60
    )
    return app, registry, capability, {
        **_SESSION_HEADERS,
        "X-MoonMind-Retrieval-Token": token,
    }


def test_session_retrieval_returns_dereferenceable_context_pack_ref(tmp_path) -> None:
    service = StubService()
    app, _registry, capability, headers = _session_app(tmp_path, service)

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context", json=_session_query(), headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "retrieval_tool_result"
        ref = body["contextPackRef"]
        assert ref == (
            f"/retrieval/capabilities/{capability.capability_id}/results/tool-1"
        )

        # The reference the host is handed actually resolves.
        resolved = client.get(ref, headers=headers)

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["transport"] == "gateway"


def test_embedded_runner_tool_exchanges_authority_without_claiming_delivery(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production runner path never exposes a reusable retrieval token."""
    service = StubService()
    app = _build_app()
    app.dependency_overrides[get_retrieval_service] = lambda: service
    registry = RetrievalCapabilityRegistry(tmp_path)
    app.state.retrieval_capability_registry = registry
    monkeypatch.setenv("OMNIGENT_HOST_RUNNER_TOKEN", "runner-root")
    binding = derive_runner_binding_token(
        "runner-root",
        host_id="host-1",
        session_id="session-1",
        generation=2_000_003,
    )
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({binding})
    ).runner_id_for_binding_token(binding)
    row = _bridge_row(
        omnigent_runner_id=runner_id,
        credential_generation=2,
        metadata_={"embedded_runner_launch": {"generation": 2_000_003}},
    )

    class Store:
        async def get_active_session_by_runner_identity(self, requested_runner_id):
            return row if requested_runner_id == runner_id else None

        async def get_session_by_provider_session_id(self, session_id):
            return row if session_id == "session-1" else None

        async def append_events(self, bridge_session_id, events):
            assert bridge_session_id == "bridge-1"

    app.dependency_overrides[get_bridge_session_store] = lambda: Store()

    with TestClient(app) as client:
        response = client.post(
            f"/retrieval/omnigent-runners/{runner_id}/tool",
            headers={"X-Omnigent-Runner-Tunnel-Token": binding},
            json={
                "query": "bounded query",
                "turnId": "turn-1",
                "toolCallId": "tool-1",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "retrieval_tool_result"
    assert body["deliveryState"] == "delivery_unknown"
    assert body["contextPack"]["transport"] == "gateway"
    assert "capability" not in body
    summary = registry.summarize_bridge_session("bridge-1")
    assert summary["aggregate"]["delivered"] == 0
    assert summary["aggregate"]["deliveryUnknown"] == 1


def test_stock_runner_mcp_registers_and_invokes_retrieval_tool(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stock runner's MCP JSON-RPC path is the production tool boundary."""
    service = StubService()
    app = _build_app()
    app.dependency_overrides[get_retrieval_service] = lambda: service
    registry = RetrievalCapabilityRegistry(tmp_path)
    app.state.retrieval_capability_registry = registry
    monkeypatch.setenv("OMNIGENT_HOST_RUNNER_TOKEN", "runner-root")
    binding = derive_runner_binding_token(
        "runner-root", host_id="host-1", session_id="session-1", generation=2_000_003
    )
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({binding})
    ).runner_id_for_binding_token(binding)
    row = _bridge_row(
        omnigent_runner_id=runner_id,
        credential_generation=2,
        metadata_={"embedded_runner_launch": {"generation": 2_000_003}},
    )

    class Store:
        async def get_active_session_by_runner_identity(self, requested_runner_id):
            return row if requested_runner_id == runner_id else None

        async def get_session_by_provider_session_id(self, session_id):
            return row if session_id == "session-1" else None

        async def append_events(self, bridge_session_id, events):
            assert bridge_session_id == "bridge-1"

    app.dependency_overrides[get_bridge_session_store] = lambda: Store()
    headers = {"X-Omnigent-Runner-Tunnel-Token": binding}
    with TestClient(app) as client:
        listed = client.post(
            "/retrieval/v1/sessions/session-1/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        called = client.post(
            "/retrieval/v1/sessions/session-1/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "moonmind_context_retrieve",
                    "arguments": {
                        "query": "bounded query",
                        "turnId": "turn-1",
                        "toolCallId": "tool-1",
                    },
                },
            },
        )
        acknowledged = client.post(
            "/retrieval/v1/sessions/session-1/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "2:delivery",
                "method": "moonmind/delivery/ack",
                "params": {"toolCallId": "tool-1"},
            },
        )
        acknowledged_retry = client.post(
            "/retrieval/v1/sessions/session-1/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "2:delivery-retry",
                "method": "moonmind/delivery/ack",
                "params": {"toolCallId": "tool-1"},
            },
        )

    assert listed.status_code == 200
    assert listed.json()["result"]["tools"][0]["name"] == "moonmind_context_retrieve"
    assert called.status_code == 200, called.text
    tool_result = json.loads(called.json()["result"]["content"][0]["text"])
    assert tool_result["kind"] == "retrieval_tool_result"
    assert tool_result["deliveryState"] == "delivery_unknown"
    assert tool_result["deliveryAcknowledgement"] == {
        "method": "moonmind/delivery/ack",
        "params": {"toolCallId": "tool-1"},
    }
    assert tool_result["contextPack"]["transport"] == "gateway"
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["result"]["deliveryState"] == "delivered"
    assert acknowledged.json()["result"]["deliveryBoundary"] == "runner_proxy_received"
    assert acknowledged_retry.status_code == 200, acknowledged_retry.text
    assert acknowledged_retry.json()["result"]["deliveryState"] == "delivered"
    assert (
        acknowledged_retry.json()["result"]["deliveryEvidenceRef"]
        == acknowledged.json()["result"]["deliveryEvidenceRef"]
    )


@pytest.mark.asyncio
async def test_embedded_bundle_and_proxy_complete_active_turn_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production embedded routes and stock proxy form one authority boundary."""
    service = StubService()
    app = FastAPI()
    app.include_router(omnigent_bridge.router)
    registry = RetrievalCapabilityRegistry(tmp_path / "registry")
    monkeypatch.setenv("OMNIGENT_HOST_RUNNER_TOKEN", "runner-root")
    binding = derive_runner_binding_token(
        "runner-root", host_id="host-1", session_id="session-1", generation=2_000_003
    )
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({binding})
    ).runner_id_for_binding_token(binding)
    row = _bridge_row(
        omnigent_runner_id=runner_id,
        credential_generation=2,
        metadata_={"embedded_runner_launch": {"generation": 2_000_003}},
    )

    class Store:
        async def get_active_session_by_runner_identity(self, requested_runner_id):
            return row if requested_runner_id == runner_id else None

        async def get_session_by_provider_session_id(self, session_id):
            return row if session_id == "session-1" else None

        async def append_events(self, bridge_session_id, events):
            assert bridge_session_id == "bridge-1"

    store = Store()
    app.dependency_overrides[omnigent_bridge._require_embedded_mode] = lambda: object()
    app.dependency_overrides[omnigent_bridge._get_bridge_store] = lambda: store
    app.dependency_overrides[get_bridge_session_store] = lambda: store
    app.dependency_overrides[get_retrieval_service] = lambda: service
    app.dependency_overrides[get_capability_registry] = lambda: registry
    headers = {"X-Omnigent-Runner-Tunnel-Token": binding}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://embedded", headers=headers
    ) as client:
        resolved = await _resolve_agent_spec_from_server(
            client, tmp_path / "specs", "session-agent", session_id="session-1"
        )
        assert resolved is not None
        manager = ProxyMcpManager("session-1", client)
        schemas = await manager.schemas_for(resolved.spec)
        output = await manager.call_tool(
            resolved.spec,
            "moonmind_context_retrieve",
            {"query": "bounded", "turnId": "turn-1", "toolCallId": "tool-1"},
        )

    assert schemas.tool_names == {"moonmind_context_retrieve"}
    result = json.loads(output)
    assert result["kind"] == "retrieval_tool_result"
    assert result["deliveryState"] == "delivered"
    assert result["deliveryBoundary"] == "runner_proxy_received"
    assert result["contextPack"]["transport"] == "gateway"
    capability = registry.live_scope_capability(
        run_id="run-1", host_id="host-1", session_id="session-1", step_id="step-1"
    )
    assert capability is not None
    assert any(
        request.get("delivery", {}).get("state") == "delivered"
        for request in registry.status(capability.capability_id)["requests"]
    )


def test_session_retrieval_passes_capability_run_identity_to_overlays(
    tmp_path,
) -> None:
    service = StubService()
    app, _registry, _capability, headers = _session_app(
        tmp_path, service, overlay_max_age_seconds=120, stale_overlay_allowed=True
    )

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context", json=_session_query(), headers=headers
        )

    assert response.status_code == 200, response.text
    call = service.calls[0]
    assert call["run_id"] == "run-1"
    assert call["overlay_max_age_seconds"] == 120
    assert call["stale_overlay_allowed"] is True
    assert call["embedding_timeout_ms"] == 2000
    assert call["search_timeout_ms"] == 3000


def test_session_retrieval_rejects_correlation_outside_capability(tmp_path) -> None:
    service = StubService()
    app, _registry, _capability, headers = _session_app(tmp_path, service)
    payload = _session_query()
    payload["correlation"]["workflow_id"] = "workflow-other"

    with TestClient(app) as client:
        response = client.post("/retrieval/context", json=payload, headers=headers)

    assert response.status_code == 403
    assert service.calls == []


def test_session_retrieval_rejects_unsupported_persist_false(tmp_path) -> None:
    service = StubService()
    app, registry, capability, headers = _session_app(tmp_path, service)

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context", json=_session_query(persist=False), headers=headers
        )

    assert response.status_code == 422
    assert "persist=false" in response.json()["detail"]
    # Rejected before reservation, so no query budget was consumed.
    assert service.calls == []
    assert registry.status(capability.capability_id)["queryCount"] == 0


def test_session_retrieval_discards_result_revoked_in_flight(tmp_path) -> None:
    """Revocation during retrieval must close authority before publishing."""
    service = StubService()
    app, registry, capability, headers = _session_app(tmp_path, service)

    original_retrieve = service.retrieve

    def revoke_mid_flight(**kwargs):
        pack = original_retrieve(**kwargs)
        registry.revoke(capability.capability_id)
        return pack

    service.retrieve = revoke_mid_flight

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context", json=_session_query(), headers=headers
        )

    assert response.status_code == 403
    assert response.json()["detail"]["classification"] == "revoked"
    # Nothing was persisted for a revoked capability.
    with pytest.raises(KeyError):
        registry.read_result(capability, "tool-1")


def test_session_retrieval_enforces_stage_deadlines(tmp_path) -> None:
    """A stalled provider must not hold the concurrency slot indefinitely."""
    service = StubService()
    app, registry, capability, headers = _session_app(
        tmp_path, service, embedding_timeout_ms=100, search_timeout_ms=100
    )

    original_retrieve = service.retrieve

    def stalled(**kwargs):
        time.sleep(1.5)
        return original_retrieve(**kwargs)

    service.retrieve = stalled

    with TestClient(app) as client:
        response = client.post(
            "/retrieval/context", json=_session_query(), headers=headers
        )

    assert response.status_code == 408
    assert response.json()["detail"]["classification"] == "stage_deadline_exhausted"
    # The reservation was released, so the slot is reusable.
    assert registry.status(capability.capability_id)["activeRequests"] == 0


def test_session_result_is_not_readable_by_another_capability(tmp_path) -> None:
    service = StubService()
    app, registry, capability, headers = _session_app(tmp_path, service)
    other_token, other = registry.issue(
        _session_budget(session_id="session-2", step_id="step-2"),
        lifetime_seconds=60,
    )

    with TestClient(app) as client:
        stored = client.post(
            "/retrieval/context", json=_session_query(), headers=headers
        )
        assert stored.status_code == 200, stored.text
        denied = client.get(
            stored.json()["contextPackRef"],
            headers={
                **_SESSION_HEADERS,
                "X-MoonMind-Session-Id": "session-2",
                "X-MoonMind-Step-Id": "step-2",
                "X-MoonMind-Retrieval-Token": other_token,
            },
        )

    assert denied.status_code == 403
    assert denied.json()["detail"]["classification"] == "identity_mismatch"
    assert other.capability_id != capability.capability_id


def test_bridge_capability_refuses_authority_inside_minimum_lifetime() -> None:
    """Authority with <30s left is refused, not clamped into a 500."""
    row = _bridge_row()
    row.effective_launch_snapshot_json["followUpRetrieval"][
        "authorityExpiresAt"
    ] = 1_000_020

    with pytest.raises(HTTPException) as refused:
        _bridge_authoritative_issue(
            row, BridgeRetrievalCapabilityIssue(lifetime_seconds=120), now=1_000_000
        )

    assert refused.value.status_code == 409
    assert "30s remaining" in refused.value.detail


@pytest.mark.asyncio
async def test_bridge_capability_issuance_is_bounded_per_scope(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried POST must not multiply the immutable query allowance."""
    monkeypatch.setenv("MOONMIND_FOLLOWUP_RETRIEVAL_COLLECTIONS", "repo,docs")
    registry = RetrievalCapabilityRegistry(tmp_path)

    class Store:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def get_bridge_session(self, bridge_session_id):
            return _bridge_row()

        async def append_events(self, bridge_session_id, events):
            self.events.extend(events)

    store = Store()

    async def issue():
        return await issue_bridge_retrieval_capability(
            "bridge-1",
            BridgeRetrievalCapabilityIssue(),
            registry=registry,
            store=store,
            user=SimpleNamespace(id="user-1"),
            service=_OwnedExecutionService(),
        )

    first = await issue()
    assert first["capabilityId"]

    with pytest.raises(HTTPException) as bounded:
        await issue()
    assert bounded.value.status_code == 409
    assert bounded.value.detail["code"] == "retrieval_capability_already_active"
    assert bounded.value.detail["capabilityId"] == first["capabilityId"]

    # After the live capability is revoked a replacement may be issued.
    registry.revoke(first["capabilityId"])
    second = await issue()
    assert second["capabilityId"] != first["capabilityId"]


@pytest.mark.asyncio
async def test_bridge_capability_issuance_denied_for_non_owner(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)

    class Store:
        async def get_bridge_session(self, bridge_session_id):
            return _bridge_row()

        async def append_events(self, bridge_session_id, events):  # pragma: no cover
            raise AssertionError("Issuance must not run for a non-owner.")

    with pytest.raises(HTTPException) as denied:
        await issue_bridge_retrieval_capability(
            "bridge-1",
            BridgeRetrievalCapabilityIssue(),
            registry=registry,
            store=Store(),
            user=SimpleNamespace(id="intruder"),
            service=_UnownedExecutionService(),
        )

    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "workflow_ownership_denied"


def _record_evidence(registry, capability, **evidence) -> None:
    """Record a bounded evidence summary the way the gateway would."""
    base = {
        "state": "succeeded",
        "correlation": {"toolCallId": "tool-x"},
        "resultCount": 1,
        "contextBytes": 100,
        "latencyMs": 42,
        "delivery": {"state": "delivery_unknown"},
        "classification": None,
        "truncated": False,
    }
    base.update(evidence)
    registry.record(capability, base)


def test_summarize_bridge_session_aggregates_follow_up_evidence(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = registry.issue(_session_budget(), lifetime_seconds=60)

    _record_evidence(registry, capability, state="succeeded", resultCount=3)
    _record_evidence(
        registry,
        capability,
        state="succeeded",
        resultCount=0,
        truncated=True,
        delivery={"state": "delivered"},
    )
    _record_evidence(
        registry,
        capability,
        state="failed",
        classification="token_budget_exhausted",
        delivery={"state": "not_delivered"},
    )
    _record_evidence(
        registry,
        capability,
        state="succeeded",
        classification="local_fallback",
        latencyMs=999,
        delivery={"state": "timed_out"},
    )

    summary = registry.summarize_bridge_session("bridge-1")

    assert summary["bridgeSessionId"] == "bridge-1"
    assert summary["capabilityCount"] == 1
    agg = summary["aggregate"]
    assert agg["requestCount"] == 4
    assert agg["succeeded"] == 3
    assert agg["failed"] == 1
    assert agg["empty"] == 1
    assert agg["truncated"] == 1
    assert agg["fallback"] == 1
    assert agg["budgetExhausted"] == 1
    assert agg["delivered"] == 1
    assert agg["notDelivered"] == 1
    assert agg["timedOut"] == 1
    assert agg["maxLatencyMs"] == 999
    assert agg["activeCapabilities"] == 1
    # Capability-level scope/collections are exposed for operator diagnostics.
    cap = summary["capabilities"][0]
    assert cap["collections"] == ["repo"]
    assert cap["scope"]["repository"] == "MoonMind"
    assert cap["policyVersion"] == "policy-7"


def test_summarize_bridge_session_merges_delivery_acknowledgement(tmp_path) -> None:
    """A delivery ack for a request is folded into it, not counted separately."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_session_budget(), lifetime_seconds=60)

    # Original successful retrieval records a provisional delivery_unknown row.
    _record_evidence(
        registry,
        capability,
        state="succeeded",
        resultCount=2,
        correlation={"toolCallId": "tool-ack"},
        delivery={"state": "delivery_unknown", "toolCallId": "tool-ack"},
    )
    # The bridge later acknowledges delivery: a second evidence row for the same
    # toolCallId. It must project onto the original request, not inflate counts.
    _record_evidence(
        registry,
        capability,
        state="delivery_updated",
        classification="delivered",
        resultCount=0,
        correlation={"toolCallId": "tool-ack"},
        delivery={"state": "delivered", "toolCallId": "tool-ack"},
    )

    summary = registry.summarize_bridge_session("bridge-1")
    agg = summary["aggregate"]
    # One logical request, counted once, with the authoritative delivery state.
    assert agg["requestCount"] == 1
    assert agg["succeeded"] == 1
    assert agg["delivered"] == 1
    assert agg["deliveryUnknown"] == 0
    # No spurious zero-result request surfaced by the acknowledgement row.
    assert agg["empty"] == 0


def test_summarize_bridge_session_excludes_other_bridge(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    registry.issue(_session_budget(), lifetime_seconds=60)
    registry.issue(
        _session_budget(bridge_session_id="bridge-2", session_id="session-2"),
        lifetime_seconds=60,
    )

    summary = registry.summarize_bridge_session("bridge-1")
    assert summary["capabilityCount"] == 1
    assert all(
        cap["scope"]["repository"] == "MoonMind"
        for cap in summary["capabilities"]
    )


@pytest.mark.asyncio
async def test_bridge_follow_up_diagnostics_requires_ownership(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    registry.issue(_session_budget(), lifetime_seconds=60)

    class Store:
        async def get_bridge_session(self, bridge_session_id):
            return _bridge_row()

    with pytest.raises(HTTPException) as denied:
        await bridge_follow_up_retrieval_diagnostics(
            "bridge-1",
            registry=registry,
            store=Store(),
            user=SimpleNamespace(id="intruder"),
            service=_UnownedExecutionService(),
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_bridge_follow_up_diagnostics_returns_projection(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_session_budget(), lifetime_seconds=60)
    _record_evidence(registry, capability, state="succeeded", resultCount=2)

    class Store:
        async def get_bridge_session(self, bridge_session_id):
            assert bridge_session_id == "bridge-1"
            return _bridge_row()

    result = await bridge_follow_up_retrieval_diagnostics(
        "bridge-1",
        registry=registry,
        store=Store(),
        user=SimpleNamespace(id="user-1"),
        service=_OwnedExecutionService(),
    )
    assert result["capabilityCount"] == 1
    assert result["aggregate"]["requestCount"] == 1
    assert result["aggregate"]["succeeded"] == 1
