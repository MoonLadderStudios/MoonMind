"""Workflow package wiring for MoonMind services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.automation.orchestrator import (
    TriggeredWorkflow,
    WorkflowConflictError,
    WorkflowRetryError,
    retry_workflow_run,
    trigger_workflow_run,
)
from moonmind.workflows.automation.repositories import (
    AutomationRepository,
    WorkflowRepository,
)
from moonmind.workflows.task_proposals.repositories import TaskProposalRepository
from moonmind.workflows.task_proposals.service import TaskProposalService
from moonmind.workflows.temporal import (
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalExecutionService,
)

def get_workflow_repository(session: AsyncSession) -> WorkflowRepository:
    """Factory helper used by FastAPI dependencies to access workflow repositories."""

    return WorkflowRepository(session)


def get_automation_repository(session: AsyncSession) -> AutomationRepository:
    """Factory helper returning the Spec Automation repository."""

    return AutomationRepository(session)



def get_agent_queue_repository(session: AsyncSession) -> AgentQueueRepository:
    """Factory helper used by queue APIs to access queue persistence."""

    return AgentQueueRepository(session)


def get_agent_queue_service(session: AsyncSession) -> AgentQueueService:
    """Factory helper returning the queue service for a DB session."""

    return AgentQueueService(get_agent_queue_repository(session))


def get_task_proposal_repository(session: AsyncSession) -> TaskProposalRepository:
    """Factory helper returning the task proposal repository."""

    return TaskProposalRepository(session)


def get_task_proposal_service(session: AsyncSession) -> TaskProposalService:
    """Factory helper returning the task proposal service."""

    return TaskProposalService(
        get_task_proposal_repository(session),
        get_agent_queue_service(session),
    )


def get_temporal_execution_service(session: AsyncSession) -> TemporalExecutionService:
    """Factory helper returning the Temporal execution lifecycle service."""

    return TemporalExecutionService(session)


def get_temporal_artifact_repository(
    session: AsyncSession,
) -> TemporalArtifactRepository:
    """Factory helper returning Temporal artifact repository for one session."""

    return TemporalArtifactRepository(session)


def get_temporal_artifact_service(session: AsyncSession) -> TemporalArtifactService:
    """Factory helper returning Temporal artifact service for one session."""

    return TemporalArtifactService(get_temporal_artifact_repository(session))


__all__ = sorted(
    [
        "AgentQueueRepository",
        "AgentQueueService",
        "AutomationRepository",
        "WorkflowRepository",
        "TaskProposalRepository",
        "TaskProposalService",
        "TemporalArtifactRepository",
        "TemporalArtifactService",
        "TemporalExecutionService",
        "TriggeredWorkflow",
        "WorkflowConflictError",
        "WorkflowRetryError",
        "get_agent_queue_repository",
        "get_agent_queue_service",
        "get_automation_repository",
        "get_workflow_repository",
        "get_task_proposal_repository",
        "get_task_proposal_service",
        "get_temporal_artifact_repository",
        "get_temporal_artifact_service",
        "get_temporal_execution_service",
        "retry_workflow_run",
        "trigger_workflow_run",
    ]
)
