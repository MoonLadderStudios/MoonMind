"""In-memory workspace materializer and publisher (reference adapters)."""

from __future__ import annotations

from moonmind.omnigent.ports.publishing import PublishResult
from moonmind.omnigent.ports.workspaces import WorkspaceHandle


class InMemoryWorkspaceMaterializer:
    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceHandle] = {}
        self._counter = 0

    async def materialize(self, bridge_session_id: str) -> WorkspaceHandle:
        self._counter += 1
        workspace_id = f"ws-{self._counter}"
        handle = WorkspaceHandle(
            workspace_id=workspace_id,
            root=f"/inmemory/{workspace_id}",
        )
        self._workspaces[workspace_id] = handle
        return handle

    async def teardown(self, workspace_id: str) -> None:
        self._workspaces.pop(workspace_id, None)

    @property
    def active(self) -> int:
        return len(self._workspaces)


class InMemoryPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(
        self, bridge_session_id: str, *, workspace_id: str
    ) -> PublishResult:
        ref = f"publish://{bridge_session_id}/{workspace_id}"
        self.published.append((bridge_session_id, workspace_id))
        return PublishResult(published=True, ref=ref)


__all__ = ["InMemoryPublisher", "InMemoryWorkspaceMaterializer"]
