"""Workflow package wiring for MoonMind services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery.repositories import SpecWorkflowRepository


def get_spec_workflow_repository(session: AsyncSession) -> SpecWorkflowRepository:
    """Factory helper used by FastAPI dependencies to access repositories."""

    return SpecWorkflowRepository(session)


__all__ = ["celery_app", "get_spec_workflow_repository", "SpecWorkflowRepository"]
