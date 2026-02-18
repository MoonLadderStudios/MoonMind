"""Service helpers for creating task step templates from existing steps."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class TaskTemplateSaveService:
    """Placeholder save service; implementation will scrub secrets and persist versions."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save_from_task(
        self,
        *,
        scope: str,
        title: str,
        description: str,
        steps: list[dict[str, Any]],
        suggested_inputs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
