"""Workspace port: materialization of a session's authoritative workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    workspace_id: str
    root: str


@runtime_checkable
class WorkspaceMaterializer(Protocol):
    async def materialize(self, bridge_session_id: str) -> WorkspaceHandle: ...

    async def teardown(self, workspace_id: str) -> None: ...


__all__ = ["WorkspaceHandle", "WorkspaceMaterializer"]
