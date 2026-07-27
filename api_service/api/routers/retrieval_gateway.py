"""Retrieval-only Gateway for worker-safe context packs."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.models import User
from moonmind.rag.service import ContextRetrievalService, RetrievalBudgetExceededError
from moonmind.rag.settings import RagRuntimeSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])

REPOSITORY_SCOPE_FILTER_KEYS = ("repo", "repository")
SESSION_SCOPE_FILTER_KEYS = frozenset(
    {
        *REPOSITORY_SCOPE_FILTER_KEYS,
        "workspace",
        "workspace_id",
        "run",
        "run_id",
        "job",
        "job_id",
        "tenant",
        "tenant_id",
        "agent_run_id",
        "agentRunId",
    }
)
SESSION_SCOPE_FILTER_KEYS_MESSAGE = ", ".join(sorted(SESSION_SCOPE_FILTER_KEYS))


@dataclass(frozen=True, slots=True)
class RetrievalAuthContext:
    auth_source: str
    allowed_repositories: tuple[str, ...]
    capabilities: tuple[str, ...]
    session_capability: "SessionRetrievalCapability | None" = None


class RetrievalCorrelation(BaseModel):
    workflow_id: str = Field(..., min_length=1, max_length=255)
    step_execution_id: str = Field(..., min_length=1, max_length=255)
    bridge_session_id: str = Field(..., min_length=1, max_length=255)
    omnigent_session_id: str = Field(..., min_length=1, max_length=255)
    host_id: str = Field(..., min_length=1, max_length=255)
    turn_id: str = Field(..., min_length=1, max_length=255)
    tool_call_id: str = Field(..., min_length=1, max_length=255)


class RetrievalBudgetSnapshot(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=255)
    repository: str = Field(..., min_length=1, max_length=512)
    run_id: str = Field(..., min_length=1, max_length=255)
    workspace_id: str = Field(..., min_length=1, max_length=255)
    collections: list[str] = Field(..., min_length=1, max_length=16)
    filters: dict[str, str] = Field(default_factory=dict)
    top_k: int = Field(default=8, ge=1, le=50)
    max_tokens: int = Field(default=4096, ge=1)
    max_latency_ms: int = Field(default=10_000, ge=1)
    max_queries: int = Field(default=8, ge=1, le=100)
    overlay_policy: str = Field(default="include", pattern="^(include|skip)$")
    fallback_allowed: bool = False
    policy_version: str = Field(..., min_length=1, max_length=128)


class IssueRetrievalCapabilityRequest(BaseModel):
    correlation: RetrievalCorrelation
    budget: RetrievalBudgetSnapshot
    expires_in_seconds: int = Field(default=900, ge=1, le=3600)


class RetrievalCapabilityResponse(BaseModel):
    capability_token: str
    capability_id: str
    expires_at: datetime
    budget_snapshot_ref: str


@dataclass(slots=True)
class SessionRetrievalCapability:
    capability_id: str
    token_digest: str
    correlation: RetrievalCorrelation
    budget: RetrievalBudgetSnapshot
    expires_at: datetime
    budget_snapshot_ref: str
    query_count: int = 0
    revoked_at: datetime | None = None
    revoked_reason: str | None = None


class SessionRetrievalCapabilityRegistry:
    """Fail-closed registry for opaque, host-bound retrieval authority.

    Only a digest of the random capability body is retained. A process restart
    invalidates all capabilities, which is safer than reviving stale authority.
    """

    def __init__(self) -> None:
        self._by_digest: dict[str, SessionRetrievalCapability] = {}
        self._by_id: dict[str, SessionRetrievalCapability] = {}
        self._dedup: dict[tuple[str, str], dict[str, object]] = {}
        self._evidence: dict[str, list[dict[str, object]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def issue(
        self, request: IssueRetrievalCapabilityRequest
    ) -> tuple[str, SessionRetrievalCapability]:
        token = f"rcap_{secrets.token_urlsafe(32)}"
        capability_id = f"rcap_{secrets.token_hex(12)}"
        now = datetime.now(tz=UTC)
        budget_digest = self._digest(request.budget.model_dump_json())
        capability = SessionRetrievalCapability(
            capability_id=capability_id,
            token_digest=self._digest(token),
            correlation=request.correlation,
            budget=request.budget,
            expires_at=now + timedelta(seconds=request.expires_in_seconds),
            budget_snapshot_ref=f"retrieval-budget:{budget_digest}",
        )
        with self._lock:
            self._by_digest[capability.token_digest] = capability
            self._by_id[capability_id] = capability
        return token, capability

    def authorize(
        self, token: str, *, host_id: str | None, session_id: str | None
    ) -> SessionRetrievalCapability:
        with self._lock:
            capability = self._by_digest.get(self._digest(token))
            if capability is None:
                raise HTTPException(status_code=401, detail="Invalid retrieval capability.")
            if capability.revoked_at is not None:
                raise HTTPException(status_code=401, detail="Retrieval capability is revoked.")
            if capability.expires_at <= datetime.now(tz=UTC):
                raise HTTPException(status_code=401, detail="Retrieval capability is expired.")
            expected = capability.correlation
            if host_id != expected.host_id or session_id != expected.omnigent_session_id:
                raise HTTPException(
                    status_code=403,
                    detail="Retrieval capability is bound to another host or session.",
                )
            return capability

    def reserve(
        self, capability: SessionRetrievalCapability, payload: "RetrievalQuery"
    ) -> dict[str, object] | None:
        key = (capability.capability_id, payload.correlation.tool_call_id)
        request_digest = self._digest(payload.model_dump_json())
        with self._lock:
            previous = self._dedup.get(key)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise HTTPException(
                        status_code=409,
                        detail="Tool-call identity was reused with a different retrieval request.",
                    )
                return previous.get("response")  # type: ignore[return-value]
            if capability.query_count >= capability.budget.max_queries:
                raise HTTPException(status_code=429, detail="Retrieval query budget exhausted.")
            capability.query_count += 1
            self._dedup[key] = {"request_digest": request_digest}
            return None

    def complete(
        self,
        capability: SessionRetrievalCapability,
        payload: "RetrievalQuery",
        response: dict[str, object],
        *,
        started_at: datetime,
    ) -> None:
        key = (capability.capability_id, payload.correlation.tool_call_id)
        pack_digest = self._digest(str(response))
        evidence = {
            "kind": "follow_up",
            "state": "delivered",
            "workflowId": payload.correlation.workflow_id,
            "stepExecutionId": payload.correlation.step_execution_id,
            "bridgeSessionId": payload.correlation.bridge_session_id,
            "omnigentSessionId": payload.correlation.omnigent_session_id,
            "hostId": payload.correlation.host_id,
            "turnId": payload.correlation.turn_id,
            "toolCallId": payload.correlation.tool_call_id,
            "queryDigest": self._digest(payload.query),
            "budgetSnapshotRef": capability.budget_snapshot_ref,
            "contextPackRef": f"retrieval-context-pack:{pack_digest}",
            "resultCount": len(response.get("items", [])),
            "latencyMs": int(
                (datetime.now(tz=UTC) - started_at).total_seconds() * 1000
            ),
            "delivery": "same_turn",
        }
        with self._lock:
            self._dedup[key]["response"] = response
            self._evidence.setdefault(capability.correlation.workflow_id, []).append(
                evidence
            )

    def revoke(self, capability_id: str, reason: str) -> None:
        with self._lock:
            capability = self._by_id.get(capability_id)
            if capability is None:
                raise HTTPException(status_code=404, detail="Retrieval capability not found.")
            capability.revoked_at = datetime.now(tz=UTC)
            capability.revoked_reason = reason

    def diagnostics(self, workflow_id: str) -> dict[str, object]:
        with self._lock:
            requests = list(self._evidence.get(workflow_id, ()))
            capabilities = [
                cap
                for cap in self._by_id.values()
                if cap.correlation.workflow_id == workflow_id
            ]
        return {
            "workflowId": workflow_id,
            "initialRequestCount": 0,
            "followUpRequestCount": len(requests),
            "requests": requests,
            "capabilities": [
                {
                    "capabilityId": cap.capability_id,
                    "queryCount": cap.query_count,
                    "maxQueries": cap.budget.max_queries,
                    "expiresAt": cap.expires_at.isoformat(),
                    "state": (
                        "revoked"
                        if cap.revoked_at
                        else "expired"
                        if cap.expires_at <= datetime.now(tz=UTC)
                        else "active"
                    ),
                }
                for cap in capabilities
            ],
        }


_session_capabilities = SessionRetrievalCapabilityRegistry()

class RetrievalQuery(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    collections: List[str] = Field(default_factory=list, max_length=16)
    filters: Dict[str, str] = Field(default_factory=dict)
    overlay_policy: str = Field(default="include", pattern="^(include|skip)$")
    budgets: Dict[str, int] = Field(default_factory=dict)
    planning_ref: Optional[str] = Field(default=None, min_length=1)
    correlation: RetrievalCorrelation | None = None

    @model_validator(mode="after")
    def validate_budget_keys(self) -> "RetrievalQuery":
        allowed = {"tokens", "latency_ms"}
        unsupported = sorted(set(self.budgets) - allowed)
        if unsupported:
            joined = ", ".join(unsupported)
            raise ValueError(
                f"Unsupported retrieval budget keys: {joined}. Allowed keys: latency_ms, tokens."
            )

        normalized_collections: list[str] = []
        seen_collections: set[str] = set()
        for collection in self.collections:
            value = str(collection).strip()
            if not value:
                raise ValueError("Retrieval collection names cannot be blank.")
            if value in seen_collections:
                continue
            seen_collections.add(value)
            normalized_collections.append(value)
        self.collections = normalized_collections

        unsupported_filters = sorted(set(self.filters) - SESSION_SCOPE_FILTER_KEYS)
        if unsupported_filters:
            joined = ", ".join(unsupported_filters)
            raise ValueError(
                "Unsupported retrieval filter keys: "
                f"{joined}. Allowed keys: {SESSION_SCOPE_FILTER_KEYS_MESSAGE}."
            )

        has_scope_filter = any(
            str(self.filters.get(key, "")).strip()
            for key in SESSION_SCOPE_FILTER_KEYS
        )
        if not has_scope_filter:
            raise ValueError(
                "Session-issued retrieval requires at least one supported "
                "scope filter to bound corpus scope. Allowed keys: "
                f"{SESSION_SCOPE_FILTER_KEYS_MESSAGE}."
            )
        return self


class IndexCollectionHealthModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    status: str
    points_count: int | None = Field(None, alias="pointsCount")
    indexed_vectors_count: int | None = Field(None, alias="indexedVectorsCount")
    segments_count: int | None = Field(None, alias="segmentsCount")
    vector_size: int | None = Field(None, alias="vectorSize")
    vector_distance: str | None = Field(None, alias="vectorDistance")
    freshness_at: str | None = Field(None, alias="freshnessAt")
    freshness_source: str | None = Field(None, alias="freshnessSource")
    freshness_status: str = Field(alias="freshnessStatus")


class IndexHealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generated_at: str = Field(alias="generatedAt")
    total_collections: int = Field(alias="totalCollections")
    total_points: int = Field(alias="totalPoints")
    collections: list[IndexCollectionHealthModel]


def get_retrieval_service(request: Request) -> ContextRetrievalService:
    cached = getattr(request.app.state, "retrieval_service", None)
    if isinstance(cached, ContextRetrievalService):
        return cached
    settings = RagRuntimeSettings.from_env()
    service = ContextRetrievalService(settings=settings)
    request.app.state.retrieval_service = service
    return service

def _bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    raw = str(authorization_header or "").strip()
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()

def _configured_retrieval_token_context(
    *,
    token: str,
    configured_token: str,
) -> RetrievalAuthContext | None:
    if not secrets.compare_digest(token, configured_token):
        return None
    allowed_repositories = tuple(
        item.strip()
        for item in os.getenv(
            "MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES",
            "",
        ).split(",")
        if item.strip()
    )
    return RetrievalAuthContext(
        auth_source="retrieval_token",
        allowed_repositories=allowed_repositories,
        capabilities=("rag",),
    )

async def authorize_retrieval_request(
    worker_token_header: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    retrieval_token_header: Optional[str] = Header(
        None,
        alias="X-MoonMind-Retrieval-Token",
    ),
    authorization_header: Optional[str] = Header(None, alias="Authorization"),
    host_id_header: Optional[str] = Header(None, alias="X-Omnigent-Host-Id"),
    session_id_header: Optional[str] = Header(None, alias="X-Omnigent-Session-Id"),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> RetrievalAuthContext:
    if worker_token_header:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Worker token authentication has been removed. "
                "Use OIDC or a scoped RetrievalGateway token."
            ),
        )

    if getattr(user, "id", None) is not None:
        return RetrievalAuthContext(
            auth_source="oidc",
            allowed_repositories=(),
            capabilities=("rag",),
        )

    capability_token = _bearer_token(authorization_header)
    if capability_token.startswith("rcap_"):
        capability = _session_capabilities.authorize(
            capability_token,
            host_id=host_id_header,
            session_id=session_id_header,
        )
        return RetrievalAuthContext(
            auth_source="session_capability",
            allowed_repositories=(capability.budget.repository,),
            capabilities=("rag",),
            session_capability=capability,
        )

    token = retrieval_token_header
    configured_token = str(os.getenv("MOONMIND_RETRIEVAL_TOKEN", "")).strip()
    if token and configured_token:
        context = _configured_retrieval_token_context(
            token=token,
            configured_token=configured_token,
        )
        if context is not None:
            return context
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid RetrievalGateway token.",
        )
    if token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="RetrievalGateway token is not configured.",
        )

    token = _bearer_token(authorization_header)
    if token and configured_token:
        context = _configured_retrieval_token_context(
            token=token,
            configured_token=configured_token,
        )
        if context is not None:
            return context
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid RetrievalGateway token.",
        )
    if token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="RetrievalGateway token is not configured.",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Retrieval authentication is required.",
    )

def _requested_repo(payload: RetrievalQuery) -> str:
    for key in REPOSITORY_SCOPE_FILTER_KEYS:
        value = str(payload.filters.get(key, "")).strip()
        if value:
            return value
    return ""

def _enforce_repo_scope(payload: RetrievalQuery, auth: RetrievalAuthContext) -> None:
    scoped_auth_sources = {"retrieval_token", "session_capability"}
    if auth.auth_source not in scoped_auth_sources or not auth.allowed_repositories:
        return
    repo = _requested_repo(payload)
    allowed = set(auth.allowed_repositories)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Repository scope is required for retrieval tokens with a "
                "configured repository allowlist."
            ),
        )
    if repo and repo not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Repository '{repo}' is not permitted for this retrieval token.",
        )


def _enforce_session_budget(
    payload: RetrievalQuery, capability: SessionRetrievalCapability
) -> None:
    budget = capability.budget
    if payload.correlation is None:
        raise HTTPException(status_code=422, detail="Session correlation is required.")
    expected = capability.correlation
    for field in (
        "workflow_id",
        "step_execution_id",
        "bridge_session_id",
        "omnigent_session_id",
        "host_id",
    ):
        if getattr(payload.correlation, field) != getattr(expected, field):
            raise HTTPException(status_code=403, detail=f"Correlation {field} is out of scope.")
    required_scope = {
        "tenant_id": budget.tenant_id,
        "repository": budget.repository,
        "run_id": budget.run_id,
        "workspace_id": budget.workspace_id,
    }
    for key, expected_value in required_scope.items():
        aliases = {
            "repository": ("repo", "repository"),
            "tenant_id": ("tenant", "tenant_id"),
            "run_id": ("run", "run_id"),
            "workspace_id": ("workspace", "workspace_id"),
        }[key]
        actual = next(
            (
                payload.filters.get(alias)
                for alias in aliases
                if payload.filters.get(alias)
            ),
            None,
        )
        if actual != expected_value:
            raise HTTPException(status_code=403, detail=f"Retrieval {key} is out of scope.")
    if payload.top_k is not None and payload.top_k > budget.top_k:
        raise HTTPException(status_code=403, detail="Requested top_k exceeds policy ceiling.")
    if payload.collections and not set(payload.collections).issubset(budget.collections):
        raise HTTPException(status_code=403, detail="Requested collection is not permitted.")
    if payload.overlay_policy != budget.overlay_policy:
        raise HTTPException(status_code=403, detail="Requested overlay policy is not permitted.")
    if payload.budgets.get("tokens", budget.max_tokens) > budget.max_tokens:
        raise HTTPException(status_code=403, detail="Requested token budget exceeds policy ceiling.")
    if payload.budgets.get("latency_ms", budget.max_latency_ms) > budget.max_latency_ms:
        raise HTTPException(status_code=403, detail="Requested latency budget exceeds policy ceiling.")


@router.post("/capabilities", response_model=RetrievalCapabilityResponse)
async def issue_retrieval_capability(
    payload: IssueRetrievalCapabilityRequest,
    _user: User = Depends(get_current_user()),
) -> RetrievalCapabilityResponse:
    token_body, capability = _session_capabilities.issue(payload)
    return RetrievalCapabilityResponse(
        capability_token=token_body,
        capability_id=capability.capability_id,
        expires_at=capability.expires_at,
        budget_snapshot_ref=capability.budget_snapshot_ref,
    )


@router.post("/capabilities/{capability_id}/revoke", status_code=204)
async def revoke_retrieval_capability(
    capability_id: str,
    reason: str = "authority_revoked",
    _user: User = Depends(get_current_user()),
) -> None:
    _session_capabilities.revoke(capability_id, reason)


@router.get("/workflows/{workflow_id}/diagnostics")
async def retrieval_diagnostics(
    workflow_id: str,
    _user: User = Depends(get_current_user()),
) -> dict[str, object]:
    return _session_capabilities.diagnostics(workflow_id)

def _enforce_retrieval_available(service: ContextRetrievalService) -> None:
    executable, reason = service.settings.retrieval_execution_reason(
        os.environ,
        preferred_transport="direct",
    )
    if executable:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Retrieval is unavailable for this managed session "
            f"(reason: {reason})."
        ),
    )

@router.get("/health")
def health(
    service: ContextRetrievalService = Depends(get_retrieval_service),
) -> Dict[str, object]:
    try:
        return service.collection_health()
    except Exception as exc:  # pragma: no cover - defensive runtime probe
        logger.warning("Retrieval health probe failed: %s", exc)
        return {"status": "degraded", "collections": []}

@router.get("/index-health", response_model=IndexHealthResponse)
async def index_health(
    service: ContextRetrievalService = Depends(get_retrieval_service),
    _user: User = Depends(get_current_user()),
) -> IndexHealthResponse:
    try:
        summary = await run_in_threadpool(service.qdrant_client.index_health)
    except Exception as exc:  # pragma: no cover - runtime dependency error path
        logger.exception("Failed to read RAG index health.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG index health is unavailable.",
        ) from exc
    return IndexHealthResponse(
        generated_at=summary.generated_at,
        total_collections=summary.total_collections,
        total_points=summary.total_points,
        collections=[
            IndexCollectionHealthModel(
                name=collection.name,
                status=collection.status,
                points_count=collection.points_count,
                indexed_vectors_count=collection.indexed_vectors_count,
                segments_count=collection.segments_count,
                vector_size=collection.vector_size,
                vector_distance=collection.vector_distance,
                freshness_at=collection.freshness_at,
                freshness_source=collection.freshness_source,
                freshness_status=collection.freshness_status,
            )
            for collection in summary.collections
        ],
    )

@router.post("/context")
async def retrieve_context_pack(
    payload: RetrievalQuery,
    service: ContextRetrievalService = Depends(get_retrieval_service),
    auth: RetrievalAuthContext = Depends(authorize_retrieval_request),
) -> Dict[str, object]:
    started_at = datetime.now(tz=UTC)
    try:
        _enforce_repo_scope(payload, auth)
        if auth.session_capability is not None:
            _enforce_session_budget(payload, auth.session_capability)
            duplicate = _session_capabilities.reserve(auth.session_capability, payload)
            if duplicate is not None:
                return duplicate
        _enforce_retrieval_available(service)
        pack = await run_in_threadpool(
            service.retrieve,
            query=payload.query,
            filters=payload.filters,
            top_k=payload.top_k
            or (
                auth.session_capability.budget.top_k
                if auth.session_capability is not None
                else service.settings.similarity_top_k
            ),
            overlay_policy=payload.overlay_policy,
            budgets=payload.budgets
            or (
                {
                    "tokens": auth.session_capability.budget.max_tokens,
                    "latency_ms": auth.session_capability.budget.max_latency_ms,
                }
                if auth.session_capability is not None
                else {}
            ),
            collections=payload.collections
            or (
                auth.session_capability.budget.collections
                if auth.session_capability is not None
                else None
            ),
            transport="direct",
            initiation_mode="session",
            planning_ref=payload.planning_ref,
        )
        pack.transport = "gateway"
        response = pack.to_dict()
        if auth.session_capability is not None:
            _session_capabilities.complete(
                auth.session_capability, payload, response, started_at=started_at
            )
        return response
    except HTTPException:
        raise
    except RetrievalBudgetExceededError as exc:
        status_code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if exc.budget_type == "tokens"
            else status.HTTP_408_REQUEST_TIMEOUT
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - runtime error path
        logger.exception("Retrieval gateway request failed.")
        raise HTTPException(
            status_code=500,
            detail="Retrieval failed due to an internal error.",
        ) from exc
