"""API router for external agent run status tracking."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external-runs", tags=["external-runs"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ExternalRunSummary(BaseModel):
    """List-level summary of one external agent run."""

    workflow_id: str = Field(..., alias="workflowId")
    run_id: Optional[str] = Field(None, alias="runId")
    agent_id: str = Field(..., alias="agentId")
    status: str
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    normalized_status: Optional[str] = Field(None, alias="normalizedStatus")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    started_at: Optional[str] = Field(None, alias="startedAt")
    closed_at: Optional[str] = Field(None, alias="closedAt")

    model_config = {"populate_by_name": True}


class ExternalRunDetail(ExternalRunSummary):
    """Full detail of one external agent run."""

    correlation_id: Optional[str] = Field(None, alias="correlationId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalRunListResponse(BaseModel):
    """Paginated list response for external runs."""

    items: list[ExternalRunSummary]
    total: int
    has_more: bool = Field(False, alias="hasMore")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Helpers — Temporal client
# ---------------------------------------------------------------------------


def _get_temporal_client() -> Any:
    """Lazy-import the shared Temporal client accessor."""

    try:
        from api_service.services.temporal.client import get_temporal_client

        return get_temporal_client
    except ImportError:
        return None


async def _query_external_runs(
    *,
    agent_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query Temporal for MoonMind.AgentRun workflows with agent_kind=external.

    Falls back to an empty list if the Temporal client is unavailable
    (e.g. in unit tests or environments without Temporal).
    """

    get_client = _get_temporal_client()
    if get_client is None:
        logger.warning("Temporal client not available; returning empty external runs")
        return []

    try:
        client = await get_client()
    except Exception:
        logger.warning("Failed to connect to Temporal; returning empty external runs", exc_info=True)
        return []

    query_parts = ['WorkflowType = "MoonMind.AgentRun"']
    if agent_id:
        escaped_agent_id = agent_id.replace('\\', '\\\\').replace('"', '\\"')
        query_parts.append(f'AgentId = "{escaped_agent_id}"')
    if status_filter:
        temporal_status = status_filter.strip().capitalize()
        if temporal_status in {"Running", "Completed", "Failed", "Canceled", "Terminated", "TimedOut"}:
            query_parts.append(f"ExecutionStatus = '{temporal_status}'")

    query = " AND ".join(query_parts)

    results: list[dict[str, Any]] = []
    try:
        async for workflow in client.list_workflows(query=query):
            memo = {}
            if hasattr(workflow, "memo") and workflow.memo:
                memo = dict(workflow.memo) if isinstance(workflow.memo, dict) else {}

            agent_kind = memo.get("agent_kind", "")
            # TODO: For a true total count, register agent_kind as a Temporal
            # custom search attribute and use client.count_workflows(query=query).
            # Post-query filtering is a pragmatic first step while the schema
            # does not include AgentKind as a search attribute.
            if agent_kind != "external":
                # Additional filter: only include external agent runs
                continue

            item: dict[str, Any] = {
                "workflowId": workflow.id,
                "runId": getattr(workflow, "run_id", None),
                "agentId": memo.get("agent_id", "unknown"),
                "status": str(getattr(workflow, "status", "unknown")).lower(),
                "providerStatus": memo.get("provider_status"),
                "normalizedStatus": memo.get("normalized_status"),
                "externalUrl": memo.get("external_url"),
                "startedAt": (
                    workflow.start_time.isoformat()
                    if hasattr(workflow, "start_time") and workflow.start_time
                    else None
                ),
                "closedAt": (
                    workflow.close_time.isoformat()
                    if hasattr(workflow, "close_time") and workflow.close_time
                    else None
                ),
                "correlationId": memo.get("correlation_id"),
                "metadata": {
                    k: v
                    for k, v in memo.items()
                    if k
                    not in {
                        "agent_kind",
                        "agent_id",
                        "provider_status",
                        "normalized_status",
                        "external_url",
                        "correlation_id",
                    }
                },
            }
            results.append(item)
            if len(results) >= limit:
                break
    except Exception:
        logger.warning("Failed to query Temporal workflows", exc_info=True)

    return results


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ExternalRunListResponse)
async def list_external_runs(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    status: Optional[str] = Query(None, description="Filter by execution status"),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
) -> dict[str, Any]:
    """List external agent runs with optional filtering."""

    runs = await _query_external_runs(
        agent_id=agent_id,
        status_filter=status,
        limit=limit + 1,
    )
    has_more = len(runs) > limit
    items = list(runs[:limit])
    return {
        "items": items,
        # NOTE: total reflects the current page, not the global count.
        # Accurate pagination requires making agent_kind a Temporal search
        # attribute and using client.count_workflows().
        "total": len(items),
        "hasMore": has_more,
    }


@router.get("/{workflow_id}", response_model=ExternalRunDetail)
async def get_external_run(workflow_id: str) -> dict[str, Any]:
    """Get detail for one external agent run by direct workflow handle lookup."""

    get_client = _get_temporal_client()
    if get_client is None:
        raise HTTPException(status_code=503, detail="Temporal client not available")

    try:
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
    except Exception:
        logger.warning("Failed to describe workflow %s", workflow_id, exc_info=True)
        raise HTTPException(status_code=404, detail=f"External run {workflow_id} not found")

    memo = {}
    if hasattr(desc, "memo") and desc.memo:
        memo = dict(desc.memo) if isinstance(desc.memo, dict) else {}

    if memo.get("agent_kind") != "external":
        raise HTTPException(status_code=404, detail=f"External run {workflow_id} not found")

    return {
        "workflowId": workflow_id,
        "runId": getattr(desc, "run_id", None),
        "agentId": memo.get("agent_id", "unknown"),
        "status": str(getattr(desc, "status", "unknown")).lower(),
        "providerStatus": memo.get("provider_status"),
        "normalizedStatus": memo.get("normalized_status"),
        "externalUrl": memo.get("external_url"),
        "startedAt": (
            desc.start_time.isoformat()
            if hasattr(desc, "start_time") and desc.start_time
            else None
        ),
        "closedAt": (
            desc.close_time.isoformat()
            if hasattr(desc, "close_time") and desc.close_time
            else None
        ),
        "correlationId": memo.get("correlation_id"),
        "metadata": {
            k: v
            for k, v in memo.items()
            if k
            not in {
                "agent_kind",
                "agent_id",
                "provider_status",
                "normalized_status",
                "external_url",
                "correlation_id",
            }
        },
    }


__all__ = ["router"]
