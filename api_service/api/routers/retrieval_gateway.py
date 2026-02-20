"""Retrieval-only Gateway for worker-safe context packs."""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from moonmind.rag.service import ContextRetrievalService
from moonmind.rag.settings import RagRuntimeSettings

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])


class RetrievalQuery(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    filters: Dict[str, str] = Field(default_factory=dict)
    overlay_policy: str = Field(default="include", pattern="^(include|skip)$")
    budgets: Dict[str, int] = Field(default_factory=dict)


def get_retrieval_service() -> ContextRetrievalService:
    settings = RagRuntimeSettings.from_env()
    return ContextRetrievalService(settings=settings)


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.post("/context")
async def retrieve_context_pack(
    payload: RetrievalQuery,
    service: ContextRetrievalService = Depends(get_retrieval_service),
) -> Dict[str, object]:
    try:
        pack = service.retrieve(
            query=payload.query,
            filters=payload.filters,
            top_k=payload.top_k or service.settings.similarity_top_k,
            overlay_policy=payload.overlay_policy,
            budgets=payload.budgets,
            transport="direct",
        )
        return pack.to_dict()
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime error path
        raise HTTPException(status_code=500, detail=str(exc)) from exc
