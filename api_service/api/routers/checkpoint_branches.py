"""Checkpoint branch graph control and query API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.services.checkpoint_branch_service import CheckpointBranchService
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchContinueModel,
    CheckpointBranchForkModel,
    CheckpointBranchGraphCreateModel,
    CheckpointBranchGraphListModel,
    CheckpointBranchGraphModel,
    CheckpointBranchPublishReadyModel,
    CheckpointBranchStateUpdateModel,
    CheckpointBranchTurnRecordModel,
)

router = APIRouter(prefix="/api/executions", tags=["checkpoint-branches"])


def _service(session: AsyncSession) -> CheckpointBranchService:
    return CheckpointBranchService(session)


def _not_found_or_bad_request(error: ValueError) -> HTTPException:
    message = str(error)
    if "not found" in message:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "checkpoint_branch_not_found", "message": message},
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "invalid_checkpoint_branch", "message": message},
    )


@router.get(
    "/{workflow_id}/checkpoint-branches",
    response_model=CheckpointBranchGraphListModel,
)
async def list_checkpoint_branches(
    workflow_id: str,
    active_only: bool = Query(False, alias="activeOnly"),
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchGraphListModel:
    items = await _service(session).list_branch_graphs(
        workflow_id=workflow_id,
        active_only=active_only,
    )
    return CheckpointBranchGraphListModel(items=items)


@router.get(
    "/{workflow_id}/checkpoint-branches/{branch_id}",
    response_model=CheckpointBranchGraphModel,
)
async def read_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchGraphModel:
    try:
        return await _service(session).read_branch_graph(
            workflow_id=workflow_id,
            branch_id=branch_id,
        )
    except ValueError as exc:
        raise _not_found_or_bad_request(exc) from exc


@router.post(
    "/{workflow_id}/checkpoint-branches",
    response_model=CheckpointBranchGraphModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkpoint_branch(
    workflow_id: str,
    payload: CheckpointBranchGraphCreateModel,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchGraphModel:
    if payload.source.workflow_id != workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "checkpoint_branch_workflow_mismatch",
                "message": "checkpoint branch source workflowId must match route workflow id",
            },
        )
    try:
        graph = await _service(session).create_branch_graph(payload)
        await session.commit()
        return graph
    except ValueError as exc:
        await session.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/continue",
    response_model=CheckpointBranchTurnRecordModel,
    status_code=status.HTTP_201_CREATED,
)
async def continue_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchContinueModel,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchTurnRecordModel:
    try:
        turn = await _service(session).continue_branch(
            workflow_id=workflow_id,
            branch_id=branch_id,
            payload=payload,
        )
        await session.commit()
        return CheckpointBranchTurnRecordModel.model_validate(turn)
    except ValueError as exc:
        await session.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/fork",
    response_model=CheckpointBranchGraphModel,
    status_code=status.HTTP_201_CREATED,
)
async def fork_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchForkModel,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchGraphModel:
    try:
        graph = await _service(session).fork_branch(
            workflow_id=workflow_id,
            branch_id=branch_id,
            payload=payload,
        )
        await session.commit()
        return graph
    except ValueError as exc:
        await session.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/archive",
    response_model=CheckpointBranchStateUpdateModel,
)
async def archive_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchStateUpdateModel:
    try:
        result = await _service(session).archive_branch(
            workflow_id=workflow_id,
            branch_id=branch_id,
        )
        await session.commit()
        return result
    except ValueError as exc:
        await session.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/publish-ready",
    response_model=CheckpointBranchStateUpdateModel,
)
async def mark_checkpoint_branch_publish_ready(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchPublishReadyModel | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> CheckpointBranchStateUpdateModel:
    try:
        result = await _service(session).mark_publish_ready(
            workflow_id=workflow_id,
            branch_id=branch_id,
            payload=payload,
        )
        await session.commit()
        return result
    except ValueError as exc:
        await session.rollback()
        raise _not_found_or_bad_request(exc) from exc
