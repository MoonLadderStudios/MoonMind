"""Retrieval-only Gateway for worker-safe context packs."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Dict, Optional

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
        "task_run_id",
        "taskRunId",
    }
)
SESSION_SCOPE_FILTER_KEYS_MESSAGE = ", ".join(sorted(SESSION_SCOPE_FILTER_KEYS))


@dataclass(frozen=True, slots=True)
class RetrievalAuthContext:
    auth_source: str
    allowed_repositories: tuple[str, ...]
    capabilities: tuple[str, ...]

class RetrievalQuery(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    filters: Dict[str, str] = Field(default_factory=dict)
    overlay_policy: str = Field(default="include", pattern="^(include|skip)$")
    budgets: Dict[str, int] = Field(default_factory=dict)
    planning_ref: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_budget_keys(self) -> "RetrievalQuery":
        allowed = {"tokens", "latency_ms"}
        unsupported = sorted(set(self.budgets) - allowed)
        if unsupported:
            joined = ", ".join(unsupported)
            raise ValueError(
                f"Unsupported retrieval budget keys: {joined}. Allowed keys: latency_ms, tokens."
            )

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
    scoped_auth_sources = {"retrieval_token"}
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
    try:
        _enforce_repo_scope(payload, auth)
        _enforce_retrieval_available(service)
        pack = await run_in_threadpool(
            service.retrieve,
            query=payload.query,
            filters=payload.filters,
            top_k=payload.top_k or service.settings.similarity_top_k,
            overlay_policy=payload.overlay_policy,
            budgets=payload.budgets,
            transport="direct",
            initiation_mode="session",
            planning_ref=payload.planning_ref,
        )
        pack.transport = "gateway"
        return pack.to_dict()
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
