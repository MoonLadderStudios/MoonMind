"""Service helpers for listing and expanding task step templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ExpandOptions:
    """Options provided when expanding template steps."""

    enforce_step_limit: bool = True


class TaskTemplateCatalogService:
    """Placeholder catalog service; full implementation added alongside API wiring."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_templates(self, *, scope: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def expand_template(
        self,
        *,
        slug: str,
        version: str,
        inputs: dict[str, Any],
        options: ExpandOptions | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
