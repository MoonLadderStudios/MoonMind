"""Workflow package wiring for MoonMind services."""

from __future__ import annotations

from moonmind.workflows.temporal.artifacts import ArtifactTransactionOwnership
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.automation.repositories import (
    AutomationRepository,
    WorkflowRepository,
)
from moonmind.workflows.temporal import (
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalExecutionService,
)

def get_workflow_repository(session: AsyncSession) -> WorkflowRepository:
    """Factory helper used by FastAPI dependencies to access workflow repositories."""

    return WorkflowRepository(session)

def get_automation_repository(session: AsyncSession) -> AutomationRepository:
    """Factory helper returning the workflow automation repository."""

    return AutomationRepository(session)

def get_temporal_execution_service(session: AsyncSession) -> TemporalExecutionService:
    """Factory helper returning the Temporal execution lifecycle service."""

    return TemporalExecutionService(session)

def get_temporal_artifact_repository(
    session: AsyncSession,
) -> TemporalArtifactRepository:
    """Factory helper returning Temporal artifact repository for one session."""

    return TemporalArtifactRepository(session)


def get_temporal_artifact_service(
    session: AsyncSession,
    *,
    transaction_ownership: ArtifactTransactionOwnership = ArtifactTransactionOwnership.STANDALONE,
) -> TemporalArtifactService:
    """Factory helper returning Temporal artifact service for one session."""

    return TemporalArtifactService(
        get_temporal_artifact_repository(session),
        transaction_ownership=transaction_ownership,
    )


__all__ = sorted(
    [
        "AutomationRepository",
        "WorkflowRepository",
        "TemporalArtifactRepository",
        "TemporalArtifactService",
        "TemporalExecutionService",
        "get_automation_repository",
        "get_workflow_repository",
        "get_temporal_artifact_repository",
        "get_temporal_artifact_service",
        "get_temporal_execution_service",
    ]
)
