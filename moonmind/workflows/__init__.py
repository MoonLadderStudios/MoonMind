"""Workflow package wiring for MoonMind services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.speckit_celery import celery_app  # noqa: F401
from moonmind.workflows.speckit_celery.orchestrator import (  # noqa: F401
    TriggeredWorkflow,
    WorkflowConflictError,
    WorkflowRetryError,
    retry_spec_workflow_run,
    trigger_spec_workflow_run,
)
from moonmind.workflows.speckit_celery.repositories import (
    SpecAutomationRepository,
    SpecWorkflowRepository,
)


def get_spec_workflow_repository(session: AsyncSession) -> SpecWorkflowRepository:
    """Factory helper used by FastAPI dependencies to access repositories."""

    return SpecWorkflowRepository(session)


def get_agent_queue_repository(session: AsyncSession) -> AgentQueueRepository:
    """Factory helper used by queue APIs to access queue persistence."""

    return AgentQueueRepository(session)


def get_agent_queue_service(session: AsyncSession) -> AgentQueueService:
    """Factory helper returning the queue service for a DB session."""

    return AgentQueueService(get_agent_queue_repository(session))


def get_spec_automation_repository(
    session: AsyncSession,
) -> SpecAutomationRepository:
    """Factory helper returning the Spec Automation repository."""

    return SpecAutomationRepository(session)


__all__ = sorted(
    [
        "AgentQueueRepository",
        "AgentQueueService",
        "SpecAutomationRepository",
        "SpecWorkflowRepository",
        "TriggeredWorkflow",
        "WorkflowConflictError",
        "WorkflowRetryError",
        "celery_app",
        "get_agent_queue_repository",
        "get_agent_queue_service",
        "get_spec_automation_repository",
        "get_spec_workflow_repository",
        "retry_spec_workflow_run",
        "trigger_spec_workflow_run",
    ]
)
