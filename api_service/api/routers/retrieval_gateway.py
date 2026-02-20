"""Retrieval-only Gateway for worker-safe context packs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api_service.auth_providers import get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.rag.service import ContextRetrievalService, RetrievalBudgetExceededError
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])


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


def get_retrieval_service(request: Request) -> ContextRetrievalService:
    cached = getattr(request.app.state, "retrieval_service", None)
    if isinstance(cached, ContextRetrievalService):
        return cached
    settings = RagRuntimeSettings.from_env()
    service = ContextRetrievalService(settings=settings)
    request.app.state.retrieval_service = service
    return service


def get_agent_queue_service(
    session=Depends(get_async_session),
) -> AgentQueueService:
    from moonmind.workflows import get_agent_queue_repository

    repository = get_agent_queue_repository(session)
    return AgentQueueService(repository)


def _bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    raw = str(authorization_header or "").strip()
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def authorize_retrieval_request(
    worker_token_header: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    authorization_header: Optional[str] = Header(None, alias="Authorization"),
    queue_service: AgentQueueService = Depends(get_agent_queue_service),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> RetrievalAuthContext:
    token = worker_token_header or _bearer_token(authorization_header)
    if token:
        try:
            policy = await queue_service.resolve_worker_token(token)
        except AgentQueueAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="Invalid worker token.") from exc
        capabilities = set(policy.capabilities)
        if not capabilities.intersection(
            {"rag", "gateway", "direct-qdrant", "rag:gateway", "rag:direct-qdrant"}
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Worker token does not have RAG retrieval capability.",
            )
        return RetrievalAuthContext(
            auth_source=policy.auth_source,
            allowed_repositories=policy.allowed_repositories,
            capabilities=policy.capabilities,
        )

    if getattr(user, "id", None) is not None:
        return RetrievalAuthContext(
            auth_source="oidc",
            allowed_repositories=(),
            capabilities=("rag",),
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Retrieval authentication is required.",
    )


def _requested_repo(payload: RetrievalQuery) -> str:
    for key in ("repo", "repository"):
        value = str(payload.filters.get(key, "")).strip()
        if value:
            return value
    return ""


def _enforce_repo_scope(payload: RetrievalQuery, auth: RetrievalAuthContext) -> None:
    if auth.auth_source != "worker_token" or not auth.allowed_repositories:
        return
    repo = _requested_repo(payload)
    allowed = set(auth.allowed_repositories)
    if repo and repo not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Repository '{repo}' is not permitted for this worker token.",
        )


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.post("/context")
async def retrieve_context_pack(
    payload: RetrievalQuery,
    service: ContextRetrievalService = Depends(get_retrieval_service),
    auth: RetrievalAuthContext = Depends(authorize_retrieval_request),
) -> Dict[str, object]:
    try:
        _enforce_repo_scope(payload, auth)
        pack = await run_in_threadpool(
            service.retrieve,
            query=payload.query,
            filters=payload.filters,
            top_k=payload.top_k or service.settings.similarity_top_k,
            overlay_policy=payload.overlay_policy,
            budgets=payload.budgets,
            transport="direct",
        )
        pack.transport = "gateway"
        return pack.to_dict()
    except HTTPException:
        raise
    except RetrievalBudgetExceededError as exc:
        status_code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
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
