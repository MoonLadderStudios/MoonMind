"""Workflow package wiring for MoonMind services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery.orchestrator import (
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


def get_spec_automation_repository(
    session: AsyncSession,
) -> SpecAutomationRepository:
    """Factory helper returning the Spec Automation repository."""

    return SpecAutomationRepository(session)


__all__ = [
    "celery_app",
    "get_spec_workflow_repository",
    "get_spec_automation_repository",
    "SpecWorkflowRepository",
    "SpecAutomationRepository",
    "trigger_spec_workflow_run",
    "retry_spec_workflow_run",
    "WorkflowConflictError",
    "WorkflowRetryError",
    "TriggeredWorkflow",
]
