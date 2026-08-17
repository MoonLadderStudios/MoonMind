"""Cleanup coordinator: release workspace/host only after terminal evidence.

Cleanup must never run while a session is still non-terminal; the use case reads
canonical session state through the port and refuses to tear down live work.
"""

from __future__ import annotations

from moonmind.omnigent.domain.session_state import is_terminal_status
from moonmind.omnigent.ports.sessions import SessionRepository
from moonmind.omnigent.ports.workspaces import WorkspaceMaterializer


class ManageCleanup:
    def __init__(
        self,
        sessions: SessionRepository,
        workspaces: WorkspaceMaterializer,
    ) -> None:
        self._sessions = sessions
        self._workspaces = workspaces

    async def cleanup(self, bridge_session_id: str, workspace_id: str) -> bool:
        """Tear down the workspace iff the session is terminal. Returns done."""

        record = await self._sessions.get(bridge_session_id)
        if record is None or not is_terminal_status(record.status):
            return False
        await self._workspaces.teardown(workspace_id)
        return True


__all__ = ["ManageCleanup"]
